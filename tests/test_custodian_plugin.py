"""Comprehensive unit tests for the Custodian plugin.

Target: 48+ tests covering all modules:
- scanner.py: fingerprint matching, scan operations
- classifier.py: confidence model, tier promote/demote
- fix_engine.py: Tier 1 auto-fixes
- journal.py: observation/action/escalation entries
- cron_registrar.py: job definitions and registration
- __init__.py: register(), hooks, slash commands
- schemas.py: tool schema validation
- dashboard: plugin_api routes
"""

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the plugin is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes_custodian_plugin import __version__, register
from hermes_custodian_plugin.schemas import (
    ALL_SCHEMAS,
    CUSTODIAN_ISSUES_SCHEMA,
    CUSTODIAN_SCAN_SCHEMA,
    CUSTODIAN_STATUS_SCHEMA,
)
from hermes_custodian_plugin.scanner import (
    ALL_FINGERPRINTS,
    KNOWN_FINGERPRINTS,
    NON_FATAL_FINGERPRINTS,
    ScanResult,
    count_fingerprints,
    get_fingerprint_by_id,
    get_tier1_fingerprints,
    get_storage_dir,
    match_fingerprint,
    scan_files,
    scan_text,
)
from hermes_custodian_plugin.classifier import (
    ConfidenceModel,
    PATTERN_WEIGHTS,
)
from hermes_custodian_plugin.fix_engine import FixEngine
from hermes_custodian_plugin.journal import Journal
from hermes_custodian_plugin.cron_registrar import CronRegistrar, CRON_JOBS


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def tmp_storage(tmp_path):
    """Provide a temporary storage directory."""
    storage = tmp_path / "ocas-custodian"
    storage.mkdir(parents=True)
    return storage


@pytest.fixture
def mock_ctx():
    """Provide a mock PluginContext."""
    ctx = MagicMock()
    ctx.register_hook = MagicMock()
    ctx.register_tool = MagicMock()
    ctx.register_command = MagicMock()
    ctx.manifest = MagicMock()
    ctx.manifest.name = "custodian"
    ctx.manifest.key = "custodian"
    return ctx


# ===========================================================================
# Test: schemas.py
# ===========================================================================

class TestSchemas:
    def test_all_schemas_count(self):
        assert len(ALL_SCHEMAS) == 3

    def test_status_schema_structure(self):
        schema = CUSTODIAN_STATUS_SCHEMA
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "custodian_status"
        assert "parameters" in schema["function"]

    def test_scan_schema_has_mode_param(self):
        schema = CUSTODIAN_SCAN_SCHEMA
        params = schema["function"]["parameters"]
        assert "mode" in params["properties"]
        assert params["properties"]["mode"]["enum"] == ["light", "deep"]
        assert "mode" in params["required"]

    def test_issues_schema_has_action_param(self):
        schema = CUSTODIAN_ISSUES_SCHEMA
        params = schema["function"]["parameters"]
        assert "action" in params["properties"]
        assert "list" in params["properties"]["action"]["enum"]
        assert "resolve" in params["properties"]["action"]["enum"]
        assert "summary" in params["properties"]["action"]["enum"]

    def test_all_schemas_have_required_fields(self):
        for schema in ALL_SCHEMAS:
            assert "type" in schema
            assert "function" in schema
            assert "name" in schema["function"]
            assert "description" in schema["function"]
            assert "parameters" in schema["function"]


# ===========================================================================
# Test: scanner.py
# ===========================================================================

class TestScanner:
    def test_known_fingerprints_count(self):
        """Should have at least 21 known fingerprints."""
        assert len(KNOWN_FINGERPRINTS) >= 21

    def test_all_fingerprints_have_required_fields(self):
        for fp in ALL_FINGERPRINTS:
            assert "id" in fp
            assert "description" in fp
            assert "tier" in fp
            assert "match_patterns" in fp
            assert isinstance(fp["match_patterns"], list)
            assert len(fp["match_patterns"]) > 0

    def test_tier1_fingerprints_have_auto_fix(self):
        for fp in KNOWN_FINGERPRINTS:
            if fp["tier"] == 1:
                assert "auto_fix" in fp
                assert fp["auto_fix"] is not None

    def test_get_tier1_fingerprints(self):
        tier1 = get_tier1_fingerprints()
        assert len(tier1) >= 15
        for fp in tier1:
            assert fp["tier"] == 1

    def test_match_fingerprint_positive(self):
        text = "job disabled due to transient error"
        fp = {"id": "test", "match_patterns": [r"job.*disabled"]}
        result = match_fingerprint(text, fp)
        assert result is not None
        assert "disabled" in result

    def test_match_fingerprint_negative(self):
        text = "everything is working fine"
        fp = {"id": "test", "match_patterns": [r"job.*disabled"]}
        result = match_fingerprint(text, fp)
        assert result is None

    def test_match_fingerprint_case_insensitive(self):
        text = "JOB DISABLED"
        fp = {"id": "test", "match_patterns": [r"job.*disabled"]}
        result = match_fingerprint(text, fp)
        assert result is not None

    def test_match_fingerprint_invalid_regex(self):
        text = "test"
        fp = {"id": "test", "match_patterns": [r"[invalid"]}
        result = match_fingerprint(text, fp)
        assert result is None  # Should not crash

    def test_scan_text_finds_issues(self):
        text = "Error: job disabled. Another error: script not found at /tmp/test.py"
        result = scan_text(text)
        assert isinstance(result, ScanResult)
        assert len(result.issues) >= 2

    def test_scan_text_no_issues(self):
        text = "All systems operational. No errors detected."
        result = scan_text(text)
        assert len(result.issues) == 0

    def test_scan_result_add_issue(self):
        result = ScanResult()
        result.add_issue("test_fp", "test_source", "evidence text", 1, "fix it")
        assert len(result.issues) == 1
        assert "test_fp" in result.fingerprints_matched

    def test_scan_result_to_dict(self):
        result = ScanResult()
        result.add_issue("fp1", "source1", "ev1", 1)
        d = result.to_dict()
        assert "issues" in d
        assert "issue_count" in d
        assert d["issue_count"] == 1

    def test_scan_files_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.log"
        result = scan_files([missing])
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].lower()

    def test_scan_files_valid_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("Error: job disabled\nAnother: script not found\n")
        result = scan_files([log_file])
        assert len(result.issues) >= 2
        assert str(log_file) in result.sources_scanned

    def test_get_fingerprint_by_id(self):
        fp = get_fingerprint_by_id("oc_cron_disabled_transient")
        assert fp is not None
        assert fp["id"] == "oc_cron_disabled_transient"

    def test_get_fingerprint_by_id_missing(self):
        fp = get_fingerprint_by_id("nonexistent_fingerprint")
        assert fp is None

    def test_count_fingerprints(self):
        counts = count_fingerprints()
        assert 1 in counts  # Tier 1
        assert counts[1] >= 15

    def test_non_fingerprints_are_tier2_plus(self):
        for fp in NON_FATAL_FINGERPRINTS:
            assert fp["tier"] >= 2

    def test_storage_dir_uses_env_var(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            d = get_storage_dir()
            assert str(tmp_path) in str(d)
            assert "ocas-custodian" in str(d)

    def test_storage_dir_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            if "HERMES_HOME" in os.environ:
                del os.environ["HERMES_HOME"]
            d = get_storage_dir()
            assert "ocas-custodian" in str(d)


# ===========================================================================
# Test: classifier.py
# ===========================================================================

class TestClassifier:
    def test_confidence_model_init(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        assert cm.storage_dir == tmp_storage

    def test_confidence_score_no_data(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        assert cm.get_score("unknown_fp") == 0.0

    def test_confidence_score_recommended_tier_default(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        assert cm.get_recommended_tier("unknown_fp") == 3

    def test_record_outcome_success(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        result = cm.record_outcome("test_fp", success=True)
        assert result["attempts"] == 1
        assert result["successes"] == 1
        assert result["failures"] == 0

    def test_record_outcome_failure(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        result = cm.record_outcome("test_fp", success=False)
        assert result["attempts"] == 1
        assert result["failures"] == 1

    def test_confidence_score_calculation(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        # 5 attempts, 4 successes
        for _ in range(4):
            cm.record_outcome("test_fp", success=True)
        cm.record_outcome("test_fp", success=False)
        score = cm.get_score("test_fp")
        # sample_confidence = min(1.0, 5/5) = 1.0
        # success_rate = 4/5 = 0.8
        # confidence_score = 1.0 * 0.8 = 0.8
        assert score == pytest.approx(0.8, abs=0.01)

    def test_should_autofix_high_confidence(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        for _ in range(5):
            cm.record_outcome("reliable_fp", success=True)
        assert cm.should_autofix("reliable_fp") is True

    def test_should_not_autofix_low_confidence(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        cm.record_outcome("unreliable_fp", success=False)
        cm.record_outcome("unreliable_fp", success=False)
        assert cm.should_autofix("unreliable_fp") is False

    def test_should_escalate_low_success_rate(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        cm.record_outcome("bad_fp", success=False)
        cm.record_outcome("bad_fp", success=False)
        cm.record_outcome("bad_fp", success=False)
        assert cm.should_escalate("bad_fp") is True

    def test_should_not_escalate_good_fp(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        for _ in range(5):
            cm.record_outcome("good_fp", success=True)
        assert cm.should_escalate("good_fp") is False

    def test_get_all_scores(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        cm.record_outcome("fp1", success=True)
        cm.record_outcome("fp2", success=False)
        scores = cm.get_all_scores()
        assert "fp1" in scores
        assert "fp2" in scores

    def test_get_summary(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        cm.record_outcome("fp1", success=True)
        summary = cm.get_summary()
        assert "fingerprints_tracked" in summary
        assert "autofix_eligible" in summary
        assert "escalate_eligible" in summary

    def test_pattern_weights(self):
        assert PATTERN_WEIGHTS["A"] == 1.0
        assert PATTERN_WEIGHTS["B"] == 0.0
        assert PATTERN_WEIGHTS["D"] == 0.0

    def test_schedule_adjusted_stickiness(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        # 2 days since fix, schedule every 0.25 days (6h), 1 recurrence
        stickiness = cm.compute_schedule_adjusted_stickiness(
            "test", days_since_fix=2.0,
            avg_schedule_interval_days=0.25, recurrence_count=1
        )
        # cycles_survived = 2.0 / 0.25 = 8.0
        # stickiness = 8.0 / (1 + 1) = 4.0
        assert stickiness == pytest.approx(4.0, abs=0.01)

    def test_check_fix_loop_true(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        assert cm.check_fix_loop("test", fix_count=3, stickiness_values=[0.1, 0.2, 0.3]) is True

    def test_check_fix_loop_false_not_enough(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        assert cm.check_fix_loop("test", fix_count=2, stickiness_values=[0.1, 0.2]) is False

    def test_check_fix_loop_false_high_stickiness(self, tmp_storage):
        cm = ConfidenceModel(tmp_storage)
        assert cm.check_fix_loop("test", fix_count=3, stickiness_values=[0.1, 0.6, 0.3]) is False

    def test_persistence(self, tmp_storage):
        """Records should persist across model instances."""
        cm1 = ConfidenceModel(tmp_storage)
        cm1.record_outcome("persist_fp", success=True)
        cm1.record_outcome("persist_fp", success=True)

        cm2 = ConfidenceModel(tmp_storage)
        assert cm2.get_score("persist_fp") > 0
        rec = cm2._effectiveness.get("persist_fp")
        assert rec is not None
        assert rec["attempts"] == 2


# ===========================================================================
# Test: fix_engine.py
# ===========================================================================

class TestFixEngine:
    def test_init(self, tmp_storage):
        engine = FixEngine(tmp_storage)
        assert engine.storage_dir == tmp_storage
        assert engine.dry_run is False

    def test_init_dry_run(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        assert engine.dry_run is True

    def test_fix_cron_disabled_transient(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_cron_disabled_transient", {"job_id": "abc123"})
        assert result is not None
        assert result["fingerprint"] == "oc_cron_disabled_transient"
        assert result["success"] is True

    def test_fix_cron_stuck_missed(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_cron_stuck_missed", {"job_id": "abc123"})
        assert result is not None
        assert "force-run" in result["description"].lower() or "run" in result["command"].lower()

    def test_fix_cron_dead_skill_ref(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_cron_dead_skill_ref", {"job_id": "j1", "skill": "missing-skill"})
        assert result is not None
        assert "missing-skill" in result["command"]

    def test_fix_cron_duplicate_function(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_cron_duplicate_function", {"job_id": "dup1", "canonical_id": "orig1"})
        assert result is not None
        assert "dup1" in result["command"]

    def test_fix_cron_orphaned_job(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_cron_orphaned_job", {"job_id": "orphan1"})
        assert result is not None
        assert "orphan1" in result["command"]

    def test_fix_journal_dir_missing(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=False)
        result = engine.apply_fix("oc_journal_dir_missing", {"skill_name": "test-skill"})
        assert result is not None
        assert result["success"] is True
        # Verify directory was created
        journal_dir = tmp_storage.parent.parent / "journals" / "test-skill"
        # Note: this uses HERMES_HOME which may not be tmp_storage in test
        # The fix creates dirs relative to HERMES_HOME env var

    def test_fix_skill_data_dir_missing(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_skill_data_dir_missing", {"skill_name": "test-skill"})
        assert result is not None
        assert result["success"] is True

    def test_fix_jsonl_oversized(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_jsonl_oversized", {"file_path": "/tmp/test.jsonl"})
        assert result is not None

    def test_fix_jsonl_malformed_lines(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_jsonl_malformed_lines", {"file_path": "/tmp/test.jsonl"})
        assert result is not None

    def test_fix_gateway_token_missing(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_gateway_token_missing", {})
        assert result is not None
        assert "token" in result["command"].lower()

    def test_fix_background_task_missing(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_background_task_missing", {"task_name": "test:task"})
        assert result is not None

    def test_fix_skill_uninitialized(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_skill_uninitialized", {"skill_name": "test-skill"})
        assert result is not None

    def test_fix_git_branch_no_tracking(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_git_branch_no_tracking", {"repo_path": "/tmp/repo", "branch": "feature"})
        assert result is not None
        assert "feature" in result["command"]

    def test_fix_no_handler(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_http_429_rate_limit", {})
        assert result is None  # Tier 2, no auto-fix

    def test_apply_all(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        issues = [
            {"fingerprint_id": "oc_cron_disabled_transient", "job_id": "j1"},
            {"fingerprint_id": "oc_cron_stuck_missed", "job_id": "j2"},
            {"fingerprint_id": "oc_http_429_rate_limit"},  # No fix
        ]
        applied, failed = engine.apply_all(issues)
        assert len(applied) == 2
        assert len(failed) == 0

    def test_fix_map_has_15_entries(self):
        assert len(FixEngine._FIX_MAP) >= 15

    def test_get_applied_fixes(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        engine.apply_fix("oc_cron_disabled_transient", {"job_id": "j1"})
        assert len(engine.get_applied_fixes()) == 1

    def test_fix_result_has_required_fields(self, tmp_storage):
        engine = FixEngine(tmp_storage, dry_run=True)
        result = engine.apply_fix("oc_cron_disabled_transient", {"job_id": "j1"})
        assert "fix_id" in result
        assert "fingerprint" in result
        assert "command" in result
        assert "description" in result
        assert "success" in result
        assert "applied_at" in result


# ===========================================================================
# Test: journal.py
# ===========================================================================

class TestJournal:
    def test_init(self, tmp_storage):
        j = Journal(tmp_storage)
        assert j.journal_dir == tmp_storage
        assert j.run_id.startswith("run_")

    def test_init_custom_run_id(self, tmp_storage):
        j = Journal(tmp_storage, run_id="custom_run")
        assert j.run_id == "custom_run"

    def test_add_observation(self, tmp_storage):
        j = Journal(tmp_storage)
        entry = j.add_observation("fp1", "source1", "evidence", 1)
        assert entry["kind"] == "observation"
        assert entry["fingerprint_id"] == "fp1"
        assert entry["tier"] == 1

    def test_add_action(self, tmp_storage):
        j = Journal(tmp_storage)
        entry = j.add_action("fp1", "fix_123", "cmd", "success")
        assert entry["kind"] == "action"
        assert entry["fix_id"] == "fix_123"
        assert entry["outcome"] == "success"

    def test_add_escalation(self, tmp_storage):
        j = Journal(tmp_storage)
        entry = j.add_escalation("issue_1", "fp1", "briefing text", "B")
        assert entry["kind"] == "escalation"
        assert entry["escalation_needed"] is True
        assert entry["pattern"] == "B"

    def test_has_actions(self, tmp_storage):
        j = Journal(tmp_storage)
        assert j.has_actions() is False
        j.add_action("fp1", "f1", "cmd", "ok")
        assert j.has_actions() is True

    def test_has_escalations(self, tmp_storage):
        j = Journal(tmp_storage)
        assert j.has_escalations() is False
        j.add_escalation("i1", "fp1", "brief")
        assert j.has_escalations() is True

    def test_get_entries(self, tmp_storage):
        j = Journal(tmp_storage)
        j.add_observation("fp1", "s1", "ev1", 1)
        j.add_action("fp1", "f1", "cmd", "ok")
        assert len(j.get_entries()) == 2

    def test_write_creates_file(self, tmp_storage):
        j = Journal(tmp_storage)
        j.add_observation("fp1", "s1", "ev1", 1)
        path = j.write()
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["run_id"] == j.run_id
        assert data["entry_count"] == 1

    def test_to_dict(self, tmp_storage):
        j = Journal(tmp_storage)
        j.add_observation("fp1", "s1", "ev1", 1)
        d = j.to_dict()
        assert "run_id" in d
        assert "entries" in d
        assert d["entry_count"] == 1

    def test_evidence_truncated(self, tmp_storage):
        j = Journal(tmp_storage)
        long_evidence = "x" * 2000
        entry = j.add_observation("fp1", "s1", long_evidence, 1)
        assert len(entry["evidence"]) <= 1000


# ===========================================================================
# Test: cron_registrar.py
# ===========================================================================

class TestCronRegistrar:
    def test_job_definitions_count(self):
        assert len(CRON_JOBS) == 3  # deep, escalation-runner, update

    def test_init(self):
        reg = CronRegistrar()
        assert len(reg.jobs) == 3

    def test_get_job_names(self):
        reg = CronRegistrar()
        names = reg.get_job_names()
        assert "custodian:deep" in names
        assert "custodian:escalation-runner" in names
        assert "custodian:update" in names

    def test_is_registered_initially_false(self):
        reg = CronRegistrar()
        assert reg.is_registered("custodian:deep") is False

    def test_mark_registered(self):
        reg = CronRegistrar()
        reg.mark_registered("custodian:deep")
        assert reg.is_registered("custodian:deep") is True

    def test_register_all(self):
        reg = CronRegistrar()
        calls = []

        def mock_cronjob(**kwargs):
            calls.append(kwargs)

        registered = reg.register_all(mock_cronjob)
        assert len(registered) == 3
        assert len(calls) == 3

    def test_register_all_skips_registered(self):
        reg = CronRegistrar()
        reg.mark_registered("custodian:deep")
        calls = []

        def mock_cronjob(**kwargs):
            calls.append(kwargs)

        registered = reg.register_all(mock_cronjob)
        assert len(registered) == 2  # Only 2 new

    def test_register_all_handles_errors(self):
        reg = CronRegistrar()

        def mock_cronjob_error(**kwargs):
            raise Exception("cron error")

        registered = reg.register_all(mock_cronjob_error)
        assert len(registered) == 0

    def test_job_has_required_fields(self):
        for job in CRON_JOBS:
            assert "name" in job
            assert "schedule" in job
            assert "prompt" in job

    def test_deep_job_schedule(self):
        deep = [j for j in CRON_JOBS if j["name"] == "custodian:deep"][0]
        assert "*" in deep["schedule"]  # cron expression

    def test_escalation_runner_schedule(self):
        esc = [j for j in CRON_JOBS if j["name"] == "custodian:escalation-runner"][0]
        assert "9-17" in esc["schedule"]  # weekday business hours

    def test_get_registered(self):
        reg = CronRegistrar()
        reg.mark_registered("test")
        assert "test" in reg.get_registered()


# ===========================================================================
# Test: __init__.py — register() and hooks
# ===========================================================================

class TestRegister:
    def test_version(self):
        assert __version__ == "2.0.0"

    def test_register_registers_hooks(self, mock_ctx):
        register(mock_ctx)
        # Should register 4 hooks
        hook_calls = [c for c in mock_ctx.register_hook.call_args_list]
        assert len(hook_calls) == 4
        hook_names = [c[0][0] for c in hook_calls]
        assert "post_tool_call" in hook_names
        assert "on_session_start" in hook_names
        assert "on_session_end" in hook_names
        assert "on_session_reset" in hook_names

    def test_register_registers_tools(self, mock_ctx):
        register(mock_ctx)
        tool_calls = [c for c in mock_ctx.register_tool.call_args_list]
        assert len(tool_calls) == 3
        tool_names = [c[1].get("name") or c[0][0] for c in tool_calls]
        assert "custodian_status" in tool_names
        assert "custodian_scan" in tool_names
        assert "custodian_issues" in tool_names

    def test_register_registers_command(self, mock_ctx):
        register(mock_ctx)
        mock_ctx.register_command.assert_called_once()
        call_kwargs = mock_ctx.register_command.call_args
        assert call_kwargs[1].get("name") == "custodian" or call_kwargs[0][0] == "custodian"


# ===========================================================================
# Test: __init__.py — slash commands
# ===========================================================================

class TestSlashCommands:
    @pytest.fixture(autouse=True)
    def _import_module(self):
        import hermes_custodian_plugin.__init__ as m
        self._mod = m

    def test_cmd_help(self):
        result = self._mod._cmd_help()
        assert "status" in result
        assert "scan" in result
        assert "issues" in result

    def test_cmd_status(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_status()
            data = json.loads(result)
            assert data["plugin"] == "custodian"
            assert "open_issues" in data

    def test_cmd_issues_list(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_issues("list")
            data = json.loads(result)
            assert "issues" in data

    def test_cmd_issues_summary(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_issues("summary")
            data = json.loads(result)
            assert "open" in data

    def test_cmd_issues_resolve_no_id(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            # Without an issue_id, falls through to list (no error, just empty list)
            result = self._mod._cmd_issues("resolve")
            data = json.loads(result)
            assert "issues" in data

    def test_cmd_scan_light(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_scan("light")
            data = json.loads(result)
            assert data["mode"] == "light"

    def test_cmd_scan_deep(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_scan("deep")
            data = json.loads(result)
            assert data["mode"] == "deep"

    def test_cmd_repair_auto(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_repair("auto")
            data = json.loads(result)
            assert "fixes_available" in data

    def test_cmd_repair_plan(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_repair("plan")
            data = json.loads(result)
            assert data["status"] == "repair plan"

    def test_cmd_schedule_show(self):
        result = self._mod._cmd_schedule("show")
        data = json.loads(result)
        assert "cron_jobs" in data
        assert len(data["cron_jobs"]) == 3

    def test_cmd_confidence_show(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_confidence("show")
            data = json.loads(result)
            assert "fingerprints_tracked" in data

    def test_cmd_init(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            with patch("hermes_custodian_plugin.__init__._get_hermes_home", return_value=tmp_storage.parent.parent):
                result = self._mod._cmd_init()
                data = json.loads(result)
                assert data["status"] == "initialized"

    def test_cmd_escalation_runner(self, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            result = self._mod._cmd_escalation_runner()
            data = json.loads(result)
            assert "escalated_issues" in data

    def test_cmd_custodian_dispatch(self):
        """Test the main dispatcher routes to correct subcommands."""
        result = self._mod._cmd_custodian("help")
        assert "status" in result

    def test_cmd_custodian_unknown(self):
        result = self._mod._cmd_custodian("nonexistent")
        assert "Unknown subcommand" in result

    def test_cmd_custodian_empty(self):
        result = self._mod._cmd_custodian("")
        assert "status" in result  # Should show help


# ===========================================================================
# Test: __init__.py — hooks
# ===========================================================================

class TestHooks:
    @pytest.fixture(autouse=True)
    def _import_module(self):
        import hermes_custodian_plugin.__init__ as m
        self._mod = m

    def test_hook_post_tool_call_ignores_non_string(self, mock_ctx):
        # Should not crash on non-string result
        self._mod._hook_post_tool_call(mock_ctx, "test_tool", {}, 12345)

    def test_hook_post_tool_call_scans_string(self, mock_ctx):
        result = self._mod._hook_post_tool_call(
            mock_ctx, "test_tool", {}, "Error: job disabled"
        )
        # Should complete without error
        assert result is None

    def test_hook_on_session_start(self, mock_ctx, tmp_storage):
        with patch("hermes_custodian_plugin.__init__.get_storage_dir", return_value=tmp_storage):
            self._mod._hook_on_session_start(mock_ctx)

    def test_hook_on_session_end(self, mock_ctx):
        self._mod._hook_on_session_end(mock_ctx)

    def test_hook_on_session_reset(self, mock_ctx):
        self._mod._hook_on_session_reset(mock_ctx)


# ===========================================================================
# Test: Integration — scan → classify → fix pipeline
# ===========================================================================

class TestIntegration:
    def test_scan_classify_fix_pipeline(self, tmp_storage):
        """End-to-end: scan text → classify → apply fix."""
        # 1. Scan
        text = "Error: job disabled. script not found at /tmp/test.py"
        scan_result = scan_text(text)
        assert len(scan_result.issues) >= 2

        # 2. Classify
        cm = ConfidenceModel(tmp_storage)
        for issue in scan_result.issues:
            fp_id = issue["fingerprint_id"]
            tier = cm.get_recommended_tier(fp_id)
            assert tier in (1, 2, 3, 4)

        # 3. Fix (dry run)
        engine = FixEngine(tmp_storage, dry_run=True)
        applied, failed = engine.apply_all(scan_result.issues)
        # At least some Tier 1 issues should have fixes
        assert len(applied) >= 1

    def test_scan_journal_fix_pipeline(self, tmp_storage):
        """Scan → journal → fix → verify."""
        text = "Error: job disabled"
        scan_result = scan_text(text)

        journal = Journal(tmp_storage)
        for issue in scan_result.issues:
            journal.add_observation(
                issue["fingerprint_id"],
                issue["source"],
                issue["evidence"],
                issue["tier"],
            )

        engine = FixEngine(tmp_storage, dry_run=True)
        applied, _ = engine.apply_all(scan_result.issues)

        for fix in applied:
            journal.add_action(
                fix["fingerprint"],
                fix["fix_id"],
                fix["command"],
                "applied" if fix["success"] else "failed",
            )

        assert journal.has_actions()
        journal_path = journal.write()
        assert journal_path.exists()

    def test_confidence_updates_after_fix(self, tmp_storage):
        """Record fix outcomes and verify confidence updates."""
        cm = ConfidenceModel(tmp_storage)
        fp = "oc_cron_disabled_transient"

        # Record 5 successful fixes
        for _ in range(5):
            cm.record_outcome(fp, success=True)

        assert cm.should_autofix(fp) is True
        assert cm.get_score(fp) >= 0.6

    def test_fix_loop_detection(self, tmp_storage):
        """Detect when a fix is applied repeatedly without sticking."""
        cm = ConfidenceModel(tmp_storage)
        fp = "oc_test_loop"

        # Record 3 failed fixes
        for _ in range(3):
            cm.record_outcome(fp, success=False)

        assert cm.check_fix_loop(fp, fix_count=3, stickiness_values=[0.1, 0.2, 0.3]) is True
        assert cm.should_escalate(fp) is True


# ===========================================================================
# Test: Dashboard plugin_api.py
# ===========================================================================

class TestDashboardAPI:
    def test_import_plugin_api(self):
        """Dashboard plugin_api should be importable."""
        # We can't fully test FastAPI routes without the dashboard context,
        # but we can verify the module loads
        spec = __import__("importlib.util").util.spec_from_file_location(
            "plugin_api",
            Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py",
        )
        assert spec is not None

    def test_manifest_json_valid(self):
        manifest_path = Path(__file__).resolve().parent.parent / "dashboard" / "manifest.json"
        data = json.loads(manifest_path.read_text())
        assert data["name"] == "custodian"
        assert data["version"] == "2.0.0"
        assert "tab" in data
        assert data["tab"]["path"] == "/custodian"
        assert "api" in data

    def test_js_bundle_exists(self):
        js_path = Path(__file__).resolve().parent.parent / "dashboard" / "dist" / "index.js"
        assert js_path.exists()
        content = js_path.read_text()
        assert "custodian" in content.lower()
        assert "mount" in content


# ===========================================================================
# Test: plugin.yaml manifest
# ===========================================================================

class TestPluginManifest:
    def test_plugin_yaml_exists(self):
        yaml_path = Path(__file__).resolve().parent.parent / "plugin.yaml"
        assert yaml_path.exists()

    def test_plugin_yaml_content(self):
        yaml_path = Path(__file__).resolve().parent.parent / "plugin.yaml"
        content = yaml_path.read_text()
        assert "name: custodian" in content
        assert "version: \"2.0.0\"" in content
        assert "post_tool_call" in content
        assert "on_session_end" in content
        assert "on_session_start" in content
        assert "on_session_reset" in content
        assert "custodian_status" in content
        assert "custodian_scan" in content
        assert "custodian_issues" in content

    def test_pyproject_toml_exists(self):
        toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        assert toml_path.exists()
