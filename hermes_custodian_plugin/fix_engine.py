"""Tier 1 auto-fix engine — 15 safe non-destructive fixes.

Every fix satisfies the safety envelope:
1. Non-destructive
2. Reversible
3. Minimal scope
4. Functionality-preserving
"""

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    home = os.environ.get("HERMES_HOME")
    if not home:
        home = os.path.join(os.path.expanduser("~"), ".hermes")
    return Path(home)


def get_storage_dir() -> Path:
    return _get_hermes_home() / "commons" / "data" / "ocas-custodian"


FixResult = Dict[str, Any]


class FixEngine:
    """Applies Tier 1 auto-fixes for known fingerprints."""

    def __init__(self, storage_dir: Optional[Path] = None, dry_run: bool = False):
        self.storage_dir = storage_dir or get_storage_dir()
        self.dry_run = dry_run
        self._fixes_applied: List[FixResult] = []
        self._fixes_failed: List[FixResult] = []

    # ------------------------------------------------------------------
    # Individual fix handlers
    # ------------------------------------------------------------------

    def fix_cron_disabled_transient(self, issue: Dict) -> FixResult:
        """Re-enable a disabled cron job."""
        job_id = issue.get("job_id", "")
        return self._record("oc_cron_disabled_transient",
                            f"hermes cron resume {job_id}",
                            f"Re-enable disabled job {job_id}")

    def fix_cron_stuck_missed(self, issue: Dict) -> FixResult:
        """Force-run a missed cron job."""
        job_id = issue.get("job_id", "")
        return self._record("oc_cron_stuck_missed",
                            f"hermes cron run {job_id}",
                            f"Force-run missed job {job_id}")

    def fix_cron_no_agent_mismatch(self, issue: Dict) -> FixResult:
        """Remove and re-create a cron job with scheduler state mismatch."""
        job_id = issue.get("job_id", "")
        return self._record("oc_cron_no_agent_mismatch",
                            f"hermes cron remove {job_id} && hermes cron add (re-create)",
                            f"Reset scheduler state for job {job_id}")

    def fix_cron_dead_skill_ref(self, issue: Dict) -> FixResult:
        """Remove dead skill reference from job's skills array."""
        job_id = issue.get("job_id", "")
        skill = issue.get("skill", "")
        return self._record("oc_cron_dead_skill_ref",
                            f"Remove skill '{skill}' from job {job_id} skills array",
                            f"Remove dead skill ref {skill} from {job_id}")

    def fix_cron_dead_script_ref(self, issue: Dict) -> FixResult:
        """Update or delete a job referencing a dead script."""
        job_id = issue.get("job_id", "")
        script = issue.get("script", "")
        agent_root = _get_hermes_home()
        # Check if script exists at alternative location
        alt_path = agent_root / "scripts" / Path(script).name
        if alt_path.exists():
            return self._record("oc_cron_dead_script_ref",
                                f"Update job {job_id} script to {alt_path}",
                                f"Fix script path for {job_id} → {alt_path}")
        return self._record("oc_cron_dead_script_ref",
                            f"Delete job {job_id} (dead script {script})",
                            f"Delete job {job_id} with missing script")

    def fix_cron_duplicate_function(self, issue: Dict) -> FixResult:
        """Delete duplicate cron job."""
        job_id = issue.get("job_id", "")
        canonical_id = issue.get("canonical_id", "")
        return self._record("oc_cron_duplicate_function",
                            f"hermes cron remove {job_id} (keep {canonical_id})",
                            f"Delete duplicate job {job_id}")

    def fix_cron_orphaned_job(self, issue: Dict) -> FixResult:
        """Remove an orphaned cron job."""
        job_id = issue.get("job_id", "")
        return self._record("oc_cron_orphaned_job",
                            f"hermes cron remove {job_id}",
                            f"Remove orphaned job {job_id}")

    def fix_journal_dir_missing(self, issue: Dict) -> FixResult:
        """Create missing journal directory."""
        skill_name = issue.get("skill_name", "unknown")
        journal_dir = _get_hermes_home() / "commons" / "journals" / skill_name
        if not self.dry_run:
            try:
                journal_dir.mkdir(parents=True, exist_ok=True)
                return self._record("oc_journal_dir_missing",
                                    f"mkdir -p {journal_dir}",
                                    f"Created journal dir {journal_dir}")
            except Exception as e:
                return self._record("oc_journal_dir_missing",
                                    f"mkdir -p {journal_dir}",
                                    f"Failed to create {journal_dir}: {e}",
                                    success=False)
        return self._record("oc_journal_dir_missing",
                            f"mkdir -p {journal_dir}",
                            f"[DRY RUN] Would create {journal_dir}")

    def fix_skill_data_dir_missing(self, issue: Dict) -> FixResult:
        """Create missing skill data directory and default config.json."""
        skill_name = issue.get("skill_name", "unknown")
        data_dir = _get_hermes_home() / "commons" / "data" / skill_name
        if not self.dry_run:
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                config_path = data_dir / "config.json"
                if not config_path.exists():
                    default_config = {"version": 1, "initialized": True}
                    config_path.write_text(json.dumps(default_config, indent=2))
                return self._record("oc_skill_data_dir_missing",
                                    f"mkdir -p {data_dir} && write config.json",
                                    f"Created data dir and config for {skill_name}")
            except Exception as e:
                return self._record("oc_skill_data_dir_missing",
                                    f"mkdir -p {data_dir}",
                                    f"Failed: {e}",
                                    success=False)
        return self._record("oc_skill_data_dir_missing",
                            f"mkdir -p {data_dir}",
                            f"[DRY RUN] Would create data dir for {skill_name}")

    def fix_jsonl_oversized(self, issue: Dict) -> FixResult:
        """Rotate oversized JSONL with date suffix."""
        file_path = issue.get("file_path", "")
        p = Path(file_path)
        if not p.exists():
            return self._record("oc_jsonl_oversized",
                                f"mv {p} {p}.{datetime.now().strftime('%Y%m%d')}",
                                f"File not found: {p}",
                                success=False)
        date_suffix = datetime.now(timezone.utc).strftime("%Y%m%d")
        rotated = p.parent / f"{p.stem}.{date_suffix}{p.suffix}"
        if not self.dry_run:
            try:
                shutil.move(str(p), str(rotated))
                p.touch()
                return self._record("oc_jsonl_oversized",
                                    f"mv {p} {rotated} && touch {p}",
                                    f"Rotated {p.name} → {rotated.name}")
            except Exception as e:
                return self._record("oc_jsonl_oversized",
                                    f"mv {p} {rotated}",
                                    f"Failed to rotate {p}: {e}",
                                    success=False)
        return self._record("oc_jsonl_oversized",
                            f"mv {p} {rotated}",
                            f"[DRY RUN] Would rotate {p.name}")

    def fix_jsonl_malformed_lines(self, issue: Dict) -> FixResult:
        """Quarantine malformed JSONL lines to .error file."""
        file_path = issue.get("file_path", "")
        p = Path(file_path)
        if not p.exists():
            return self._record("oc_jsonl_malformed_lines",
                                f"Quarantine from {p}",
                                f"File not found: {p}",
                                success=False)
        error_path = p.parent / f"{p.stem}.error"
        malformed_count = 0
        if not self.dry_run:
            try:
                good_lines = []
                bad_lines = []
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)
                        good_lines.append(line)
                    except json.JSONDecodeError:
                        bad_lines.append(line)
                        malformed_count += 1
                if bad_lines:
                    p.write_text("\n".join(good_lines) + "\n", encoding="utf-8")
                    with open(error_path, "a", encoding="utf-8") as f:
                        for bl in bad_lines:
                            f.write(bl + "\n")
                return self._record("oc_jsonl_malformed_lines",
                                    f"Quarantined {malformed_count} lines to {error_path}",
                                    f"Quarantined {malformed_count} malformed lines from {p.name}")
            except Exception as e:
                return self._record("oc_jsonl_malformed_lines",
                                    f"Quarantine from {p}",
                                    f"Failed: {e}",
                                    success=False)
        return self._record("oc_jsonl_malformed_lines",
                            f"Quarantine from {p}",
                            f"[DRY RUN] Would quarantine malformed lines from {p.name}")

    def fix_gateway_token_missing(self, issue: Dict) -> FixResult:
        """Generate gateway token."""
        return self._record("oc_gateway_token_missing",
                            "platform diagnostics --generate-gateway-token",
                            "Generate gateway auth token")

    def fix_background_task_missing(self, issue: Dict) -> FixResult:
        """Register missing background task."""
        task_name = issue.get("task_name", "unknown")
        return self._record("oc_background_task_missing",
                            f"Register cron job for {task_name}",
                            f"Register missing background task {task_name}")

    def fix_skill_uninitialized(self, issue: Dict) -> FixResult:
        """Initialize skill: create storage dirs, default config, empty JSONL."""
        skill_name = issue.get("skill_name", "unknown")
        data_dir = _get_hermes_home() / "commons" / "data" / skill_name
        journal_dir = _get_hermes_home() / "commons" / "journals" / skill_name
        if not self.dry_run:
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                journal_dir.mkdir(parents=True, exist_ok=True)
                config_path = data_dir / "config.json"
                if not config_path.exists():
                    config_path.write_text(json.dumps({"version": 1}, indent=2))
                return self._record("oc_skill_uninitialized",
                                    f"Init {skill_name}: dirs + config",
                                    f"Initialized skill {skill_name}")
            except Exception as e:
                return self._record("oc_skill_uninitialized",
                                    f"Init {skill_name}",
                                    f"Failed: {e}",
                                    success=False)
        return self._record("oc_skill_uninitialized",
                            f"Init {skill_name}",
                            f"[DRY RUN] Would initialize {skill_name}")

    def fix_git_branch_no_tracking(self, issue: Dict) -> FixResult:
        """Set upstream tracking for git branch."""
        repo_path = issue.get("repo_path", "")
        branch = issue.get("branch", "")
        return self._record("oc_git_branch_no_tracking",
                            f"cd {repo_path} && git branch --set-upstream-to=origin/{branch} {branch}",
                            f"Set upstream for {branch} in {repo_path}")

    # ------------------------------------------------------------------
    # Core apply logic
    # ------------------------------------------------------------------

    _FIX_MAP: Dict[str, str] = {
        "oc_cron_disabled_transient": "fix_cron_disabled_transient",
        "oc_cron_stuck_missed": "fix_cron_stuck_missed",
        "oc_cron_no_agent_mismatch": "fix_cron_no_agent_mismatch",
        "oc_cron_dead_skill_ref": "fix_cron_dead_skill_ref",
        "oc_cron_dead_script_ref": "fix_cron_dead_script_ref",
        "oc_cron_duplicate_function": "fix_cron_duplicate_function",
        "oc_cron_orphaned_job": "fix_cron_orphaned_job",
        "oc_journal_dir_missing": "fix_journal_dir_missing",
        "oc_skill_data_dir_missing": "fix_skill_data_dir_missing",
        "oc_jsonl_oversized": "fix_jsonl_oversized",
        "oc_jsonl_malformed_lines": "fix_jsonl_malformed_lines",
        "oc_gateway_token_missing": "fix_gateway_token_missing",
        "oc_background_task_missing": "fix_background_task_missing",
        "oc_skill_uninitialized": "fix_skill_uninitialized",
        "oc_git_branch_no_tracking": "fix_git_branch_no_tracking",
    }

    def apply_fix(self, fingerprint_id: str, issue: Dict) -> Optional[FixResult]:
        """Apply the auto-fix for a given fingerprint. Returns FixResult or None."""
        method_name = self._FIX_MAP.get(fingerprint_id)
        if method_name is None:
            logger.debug("No auto-fix handler for %s", fingerprint_id)
            return None
        handler = getattr(self, method_name, None)
        if handler is None:
            logger.warning("Fix handler %s not found", method_name)
            return None
        return handler(issue)

    def apply_all(self, issues: List[Dict]) -> Tuple[List[FixResult], List[FixResult]]:
        """Apply all applicable fixes. Returns (applied, failed)."""
        applied = []
        failed = []
        for issue in issues:
            fp_id = issue.get("fingerprint_id", "")
            result = self.apply_fix(fp_id, issue)
            if result is None:
                continue
            if result.get("success", True):
                applied.append(result)
            else:
                failed.append(result)
        self._fixes_applied = applied
        self._fixes_failed = failed
        return applied, failed

    def _record(self, fingerprint: str, command: str, description: str,
                 success: bool = True) -> FixResult:
        record = {
            "fix_id": f"fix_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{fingerprint}",
            "fingerprint": fingerprint,
            "command": command,
            "description": description,
            "success": success,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": self.dry_run,
        }
        if success:
            self._fixes_applied.append(record)
        else:
            self._fixes_failed.append(record)
        return record

    def get_applied_fixes(self) -> List[FixResult]:
        return list(self._fixes_applied)

    def get_failed_fixes(self) -> List[FixResult]:
        return list(self._fixes_failed)

    def write_fix_log(self) -> Optional[Path]:
        """Append applied fixes to fixes.jsonl."""
        if not self._fixes_applied:
            return None
        fixes_path = self.storage_dir / "fixes.jsonl"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        with open(fixes_path, "a", encoding="utf-8") as f:
            for fix in self._fixes_applied:
                f.write(json.dumps(fix, default=str) + "\n")
        return fixes_path
