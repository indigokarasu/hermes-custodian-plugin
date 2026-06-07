"""Scanner: 21 known issue fingerprints for Custodian.

Each fingerprint has: id, description, tier, match_patterns, source, auto_fix.
Fingerprints match against gateway logs, cron run logs, skill journals, and OCAS data directories.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    """Resolve HERMES_HOME — must use env var, never __file__."""
    home = os.environ.get("HERMES_HOME")
    if not home:
        home = os.path.join(os.path.expanduser("~"), ".hermes")
    return Path(home)


def _get_agent_root() -> Path:
    return _get_hermes_home()


def get_storage_dir() -> Path:
    return _get_agent_root() / "commons" / "data" / "ocas-custodian"


def get_journal_dir() -> Path:
    return _get_agent_root() / "commons" / "journals" / "ocas-custodian"


# ---------------------------------------------------------------------------
# The 21 known fingerprints
# ---------------------------------------------------------------------------

KNOWN_FINGERPRINTS: List[Dict[str, Any]] = [
    {
        "id": "oc_cron_disabled_transient",
        "description": "Cron job transiently disabled, likely from a failed run or timeout",
        "tier": 1,
        "match_patterns": [r"job.*disabled", r"enabled.*false"],
        "source": "cron_log",
        "auto_fix": "Re-enable the disabled cron job via hermes cron resume",
    },
    {
        "id": "oc_cron_stuck_missed",
        "description": "Cron job missed its scheduled run window",
        "tier": 1,
        "match_patterns": [r"missed.*schedule", r"stuck.*cron", r"overdue.*run"],
        "source": "cron_log",
        "auto_fix": "Force-run the missed job",
    },
    {
        "id": "oc_cron_no_agent_mismatch",
        "description": "Scheduler no_agent state mismatch between in-memory and jobs.json",
        "tier": 1,
        "match_patterns": [r"no_agent=True but no script is set", r"no_agent.*script.*not set"],
        "source": "cron_log",
        "auto_fix": "Remove and re-create the cron job to reset scheduler state",
    },
    {
        "id": "oc_cron_dead_skill_ref",
        "description": "Cron job references a skill directory that does not exist",
        "tier": 1,
        "match_patterns": [r"skill.*not found", r"skill directory.*missing", r"no such file.*skills/"],
        "source": "cron_log",
        "auto_fix": "Remove dead skill reference from job's skills array, or delete job",
    },
    {
        "id": "oc_cron_dead_script_ref",
        "description": "Cron job references a script file that does not exist",
        "tier": 1,
        "match_patterns": [r"script.*not found", r"no such file.*script", r"cannot execute.*script"],
        "source": "cron_log",
        "auto_fix": "Update script path or delete job",
    },
    {
        "id": "oc_cron_duplicate_function",
        "description": "Two or more cron jobs perform the same function",
        "tier": 1,
        "match_patterns": [r"duplicate.*cron", r"identical.*job"],
        "source": "ocas_data",
        "auto_fix": "Delete duplicate job (keep canonical name/earliest ID)",
    },
    {
        "id": "oc_cron_orphaned_job",
        "description": "Cron job not declared in any SKILL.md and has never run",
        "tier": 1,
        "match_patterns": [r"job.*not declared.*SKILL.md", r"orphaned.*cron"],
        "source": "ocas_data",
        "auto_fix": "Remove orphaned cron job",
    },
    {
        "id": "oc_journal_dir_missing",
        "description": "Skill journal directory missing, blocking journal writes",
        "tier": 1,
        "match_patterns": [r"ENOENT.*journals", r"journal.*directory.*missing", r"cannot write.*journal"],
        "source": "skill_journal",
        "auto_fix": "Create journal directory",
    },
    {
        "id": "oc_skill_data_dir_missing",
        "description": "Skill data directory or config.json missing",
        "tier": 1,
        "match_patterns": [r"ENOENT.*data/ocas-", r"data directory.*missing", r"config\.json.*not found"],
        "source": "ocas_data",
        "auto_fix": "Create directory and default config.json",
    },
    {
        "id": "oc_jsonl_oversized",
        "description": "JSONL log file exceeded max_records threshold",
        "tier": 1,
        "match_patterns": [r"file size exceeded", r"jsonl.*too large", r"rotation needed"],
        "source": "ocas_data",
        "auto_fix": "Rotate with date suffix",
    },
    {
        "id": "oc_jsonl_malformed_lines",
        "description": "JSONL file contains malformed JSON lines",
        "tier": 1,
        "match_patterns": [r"JSON parse error", r"malformed.*jsonl", r"invalid JSON.*line"],
        "source": "ocas_data",
        "auto_fix": "Quarantine malformed lines to .error file",
    },
    {
        "id": "oc_gateway_token_missing",
        "description": "Gateway authentication token missing or invalid",
        "tier": 1,
        "match_patterns": [r"gateway token.*missing", r"authentication.*gateway.*failed", r"no gateway token"],
        "source": "gateway_log",
        "auto_fix": "Generate gateway token",
    },
    {
        "id": "oc_background_task_missing",
        "description": "Declared background task not found in cron registry",
        "tier": 1,
        "match_patterns": [r"missing.*cron.*job", r"background task.*not registered"],
        "source": "ocas_data",
        "auto_fix": "Register missing cron entry per SKILL.md declaration",
    },
    {
        "id": "oc_skill_uninitialized",
        "description": "Installed skill has no data directory, config, or journal directory",
        "tier": 1,
        "match_patterns": [r"skill.*uninitialized", r"missing.*data directory.*config"],
        "source": "ocas_data",
        "auto_fix": "Create storage dirs, default config, empty JSONL",
    },
    {
        "id": "oc_cron_next_run_at_none",
        "description": "Cron job scheduler state stale (next_run_at not recalculated)",
        "tier": 1,
        "match_patterns": [r"next_run_at.*None", r"next_run_at.*null"],
        "source": "cron_log",
        "auto_fix": "Pause and resume the job to force scheduler recalculation",
    },
    {
        "id": "oc_cron_stale_empty_error",
        "description": "Stale error state: status=error but last_error empty and consecutive_failures=0",
        "tier": 1,
        "match_patterns": [r"status.*error.*last_error.*(null|empty)"],
        "source": "cron_log",
        "auto_fix": "Pause and resume the job to reset stale scheduler state",
    },
    {
        "id": "oc_google_oauth_refresh_400",
        "description": "Google OAuth token invalid — re-authorization needed",
        "tier": 1,
        "match_patterns": [r"invalid_grant", r"token has been expired or revoked", r"gmail\.api.*401"],
        "source": "cron_log",
        "auto_fix": "Re-authorize via google OAuth init script",
    },
    {
        "id": "oc_git_branch_no_tracking",
        "description": "Skill repo on feature branch with no upstream tracking",
        "tier": 1,
        "match_patterns": [r"There is no tracking information for the current branch", r"no tracking information.*branch"],
        "source": "cron_log",
        "auto_fix": "Set upstream tracking via git branch --set-upstream-to",
    },
    {
        "id": "oc_http_429_concurrent",
        "description": "Too many concurrent API requests from simultaneous cron jobs",
        "tier": 1,
        "match_patterns": [r"too many concurrent requests"],
        "source": "cron_log",
        "auto_fix": "Stagger cron schedules: offset each job's start minute",
    },
    {
        "id": "oc_http_401_nous_api_key",
        "description": "Http 401 from Nous API — bypass expired credential",
        "tier": 1,
        "match_patterns": [r"401.*nous", r"Nous.*credential.*expired"],
        "source": "cron_log",
        "auto_fix": "Set auxiliary provider to openrouter in config.yaml",
    },
    {
        "id": "oc_vision_model_incompatible",
        "description": "Vision model incompatible — provider mismatch",
        "tier": 1,
        "match_patterns": [r"vision.*incompatible", r"vision.*provider.*mismatch"],
        "source": "gateway_log",
        "auto_fix": "Set auxiliary.vision.provider to explicit provider",
    },
]

# Tier 2 fingerprints (detected but NOT auto-fixed)
NON_FATAL_FINGERPRINTS: List[Dict[str, Any]] = [
    {
        "id": "oc_cron_timeout",
        "description": "Cron job hit idle timeout",
        "tier": 2,
        "match_patterns": [r"idle for.*limit.*s", r"TimeoutError.*Cron job", r"timed out after"],
        "source": "cron_log",
    },
    {
        "id": "oc_http_429_rate_limit",
        "description": "HTTP 429 rate limit from LLM provider",
        "tier": 2,
        "match_patterns": [r"HTTP 429: Provider returned error", r"429.*rate-limit", r"temporarily rate-limited"],
        "source": "cron_log",
    },
    {
        "id": "oc_http_502_provider_unavailable",
        "description": "OpenRouter HTTP 502 provider_unavailable",
        "tier": 2,
        "match_patterns": [r"HTTP 502.*provider_unavailable", r"error_type.*provider_unavailable"],
        "source": "errors_log",
    },
    {
        "id": "oc_disk_full",
        "description": "Root filesystem is 100% full",
        "tier": 3,
        "match_patterns": [r"database or disk is full", r"No space left on device"],
        "source": "gateway_log",
    },
    {
        "id": "oc_gateway_process_down",
        "description": "Gateway process not running",
        "tier": 4,
        "match_patterns": [r"gateway.*not running", r"ECONNREFUSED.*18789"],
        "source": "gateway_log",
    },
    {
        "id": "oc_mcp_stdio_parse_error",
        "description": "MCP server stdout contains non-JSON content",
        "tier": 2,
        "match_patterns": [r"Failed to parse JSONRPC message from server", r"input_value=.*\\\\x1b"],
        "source": "errors_log",
    },
    {
        "id": "oc_cron_null_field_crash",
        "description": "execute_code crashes on NoneType f-strings from jobs.json",
        "tier": 2,
        "match_patterns": [r"TypeError.*NoneType.*format"],
        "source": "execute_code",
    },
    {
        "id": "oc_state_db_oversized",
        "description": "state.db exceeds 10GB",
        "tier": 2,
        "match_patterns": [r"state\.db.*(oversized|too large|10GB)"],
        "source": "ocas_data",
    },
    {
        "id": "oc_old_path_reference",
        "description": "Legacy /usr/local/lib/hermes-agent/ path references",
        "tier": 2,
        "match_patterns": [r"/usr/local/lib/hermes-agent/"],
        "source": "cron_log",
    },
    {
        "id": "oc_read_file_too_large",
        "description": "read_file 100K character limit exceeded",
        "tier": 2,
        "match_patterns": [r"exceeds the safety limit", r"Read produced.*characters which exceeds"],
        "source": "cron_log",
    },
]


ALL_FINGERPRINTS = KNOWN_FINGERPRINTS + NON_FATAL_FINGERPRINTS


class ScanResult:
    """Result of a fingerprint scan pass."""

    def __init__(self):
        self.issues: List[Dict[str, Any]] = []
        self.fingerprints_matched: List[str] = []
        self.sources_scanned: List[str] = []
        self.errors: List[str] = []

    def add_issue(self, fingerprint_id: str, source: str, evidence: str,
                  tier: int, auto_fix: Optional[str] = None):
        self.issues.append({
            "fingerprint_id": fingerprint_id,
            "source": source,
            "evidence": evidence,
            "tier": tier,
            "auto_fix": auto_fix,
        })
        if fingerprint_id not in self.fingerprints_matched:
            self.fingerprints_matched.append(fingerprint_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issues": self.issues,
            "fingerprints_matched": self.fingerprints_matched,
            "sources_scanned": self.sources_scanned,
            "errors": self.errors,
            "issue_count": len(self.issues),
        }


def match_fingerprint(text: str, fingerprint: Dict[str, Any]) -> Optional[str]:
    """Match a fingerprint's patterns against text. Returns first matching pattern or None."""
    for pattern in fingerprint.get("match_patterns", []):
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return pattern
        except re.error:
            logger.warning("Invalid regex in fingerprint %s: %s", fingerprint["id"], pattern)
    return None


def scan_text(text: str, fingerprints: Optional[List[Dict]] = None) -> ScanResult:
    """Scan text against all fingerprints. Returns matched issues."""
    result = ScanResult()
    if fingerprints is None:
        fingerprints = ALL_FINGERPRINTS

    for fp in fingerprints:
        matched = match_fingerprint(text, fp)
        if matched:
            result.add_issue(
                fingerprint_id=fp["id"],
                source=fp.get("source", "unknown"),
                evidence=text[:500],
                tier=fp.get("tier", 3),
                auto_fix=fp.get("auto_fix"),
            )
    return result


def scan_files(file_paths: List[Path], fingerprints: Optional[List[Dict]] = None) -> ScanResult:
    """Scan multiple files against fingerprints. Concatenates results."""
    combined = ScanResult()
    for path in file_paths:
        try:
            if not path.exists():
                combined.errors.append(f"File not found: {path}")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            result = scan_text(text, fingerprints)
            combined.issues.extend(result.issues)
            combined.fingerprints_matched.extend(result.fingerprints_matched)
            combined.errors.extend(result.errors)
            combined.sources_scanned.append(str(path))
        except Exception as e:
            combined.errors.append(f"Error scanning {path}: {e}")
    return combined


def get_tier1_fingerprints() -> List[Dict[str, Any]]:
    """Return only Tier 1 (auto-fix) fingerprints."""
    return [fp for fp in ALL_FINGERPRINTS if fp.get("tier") == 1]


def get_fingerprint_by_id(fingerprint_id: str) -> Optional[Dict[str, Any]]:
    """Look up a fingerprint by its ID."""
    for fp in ALL_FINGERPRINTS:
        if fp["id"] == fingerprint_id:
            return fp
    return None


def count_fingerprints() -> Dict[str, int]:
    """Return counts by tier."""
    counts = {}
    for fp in ALL_FINGERPRINTS:
        tier = fp.get("tier", 0)
        counts[tier] = counts.get(tier, 0) + 1
    return counts
