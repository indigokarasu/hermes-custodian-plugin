"""Scanner: 21 known issue fingerprints for Custodian.

Each fingerprint has: id, description, tier, match_patterns, source, auto_fix.
Fingerprints match against gateway logs, cron run logs, skill journals, and OCAS data directories.
"""

import datetime
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
        "description": "Stale error state: status=error but last_error empty and failure_streak=0",
        "tier": 1,
        "match_patterns": [r"status.*error.*last_error.*(null|empty)"],
        "source": "cron_log",
        "auto_fix": "Pause and resume the job to reset stale scheduler state",
    },
    {
        "id": "oc_cron_failure_streak",
        "description": "Cron job has 3+ consecutive failures — needs investigation or auto-pause",
        "tier": 1,
        "match_patterns": [r"failure_streak.*[3-9]", r"failure_streak.*1[0-9]"],
        "source": "cron_log",
        "auto_pause": "Pause job via cronjob(action=pause) if failure_streak >= 5",
    },
    {
        "id": "oc_cron_execute_code_in_cron",
        "description": "Cron job attempted execute_code — blocked in cron mode, needs redesign",
        "tier": 1,
        # Require the CRON-MODE denial text specifically. A bare
        # `execute_code.*blocked` also matches an interactive session whose
        # approve prompt timed out ("BLOCKED: execute_code script timed out
        # without user response") — correct behaviour, not a design flaw — and
        # it was reported as a Tier-1 cron defect. The distinctive cron-mode
        # wording below is what actually identifies this condition.
        "match_patterns": [
            r"execute_code runs arbitrary local Python.*Cron jobs run without a user present",
            r"cron_mode.*deny",
            r"approval_pending.*execute_code",
        ],
        "source": "cron_log",
        "auto_flag": "Flag as needs_redesign — replace execute_code with terminal() or no_agent script",
    },
    {
        "id": "oc_cron_google_mcp_down",
        "description": "Cron job failed because Google Workspace MCP is unavailable",
        "tier": 1,
        "match_patterns": [
            r"google.*workspace.*mcp.*(unavailable|not.*running|not.*registered)",
            r"mcp.*server.*(not.*responding|not.*available)",
            r"failed to parse JSONRPC message from server",
        ],
        "source": "cron_log",
        "auto_fix": "Restart gateway to reconnect MCP servers",
    },
    {
        "id": "oc_cron_google_auth_expired",
        "description": "Cron job failed due to expired/revoked Google OAuth token",
        "tier": 1,
        "match_patterns": [r"invalid_grant", r"token has been expired or revoked", r"gmail\.api.*401"],
        "source": "cron_log",
        "auto_fix": "Re-authorize Google OAuth for affected account",
    },
    {
        "id": "oc_cron_rate_limit_429",
        "description": "Cron job hit HTTP 429 rate limit",
        "tier": 2,
        "match_patterns": [r"HTTP 429", r"429.*rate.limit", r"too many concurrent requests"],
        "source": "cron_log",
        "auto_fix": "Stagger cron schedules or reduce max_parallel_jobs",
    },
    {
        "id": "oc_cron_timeout",
        "description": "Cron job hit idle or upstream timeout",
        "tier": 2,
        # A bare `timed out after` matched 126 lines, 111 of which were
        # HOOK-CALLBACK timeouts ("Hook 'x' callback timed out after 30s") —
        # a plugin-slowness condition with its own remedy, not a cron job
        # timing out. Anchoring on the TOOL-EXECUTOR emitter is what actually
        # distinguishes a job's tool timing out from a plugin hook timing out;
        # a bare cron-session id does not (hooks fire inside cron sessions too).
        "match_patterns": [
            r"idle for.*limit.*s",
            r"TimeoutError",
            r"upstream idle timeout",
            r"agent\.tool_executor.*timed out after",
        ],
        "source": "cron_log",
    },
    {
        "id": "oc_cron_response_truncated",
        "description": "Cron job response was truncated — likely output too large or model limit hit",
        "tier": 2,
        "match_patterns": [r"Response remained truncated", r"truncated after.*continuation"],
        "source": "cron_log",
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
    # NOTE: oc_cron_timeout is intentionally NOT duplicated here — it already exists in
    # KNOWN_FINGERPRINTS. Two entries with the same id made scan_text emit the SAME issue
    # twice per scan (double-reporting in every report and journal write).
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


#: Lines that *report on* a fingerprint rather than being one. Custodian's own scans,
#: dashboards, journal dumps and cron reports quote matched patterns verbatim, so a naive
#: substring scan re-discovers its own output on the next run and reports a fixed issue as
#: still live. Skip any line that carries one of these markers.
#: NOTE: do NOT add a bare "custodian:" marker here. Cron logs name jobs like
#: 'custodian:update', so that marker suppresses real failures — verified: it hid
#: 3 of 3 genuine "Job 'custodian:X' failed" lines. The markers below are specific
#: to custodian's *report* text and still catch genuine echo lines.
_ECHO_MARKERS = (
    "oc_cron_scan",
    "fingerprint_id",
    "FINGERPRINT:",
    "match_patterns",
    "evidence=",
    "auto_fix",
)

#: Path fragments whose contents must never be scanned as evidence (the scanner reads its
#: own storage/journal/output directories back).
_ECHO_PATHS = (
    "commons/data/ocas-custodian",
    "commons/journals/ocas-custodian",
    "plugins/custodian/",
    "cron/output/",
)


def _is_echo_line(line: str) -> bool:
    """True when a log line is a report *about* a fingerprint, not an occurrence of one."""
    low = line.lower()
    return any(marker.lower() in low for marker in _ECHO_MARKERS)


def is_echo_path(path: Any) -> bool:
    """True when a file lives in a directory the scanner itself writes to."""
    p = str(path).replace("\\", "/").lower()
    return any(frag in p for frag in _ECHO_PATHS)


def _first_matching_line(text: str, pattern: str) -> Optional[str]:
    """Return the first line that actually matches *pattern* (so evidence is attributable)."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None
    for line in text.splitlines():
        if rx.search(line) and not _is_echo_line(line):
            return line.strip()
    return None


def _line_recency_ok(line: str, max_age_hours: Optional[float]) -> bool:
    """True when a log line is recent enough to be worth reporting.

    Logs are append-only and custodian scans the whole tail every cycle, so without a
    recency window a single old error re-reports on every run forever.

    Continuation lines (traceback bodies, multi-line exception text) carry NO timestamp
    of their own. Failing open on those — as this once did — let a 3-day-old Gemini 429
    keep reporting as live, because the pattern matched a continuation line rather than
    the dated header above it. Callers must therefore resolve the owning timestamp first
    and pass it in via ``_LineFilter``; a bare line with no context is treated as stale.
    """
    if not max_age_hours or max_age_hours <= 0:
        return True
    ts = _parse_line_timestamp(line)
    if ts is None:
        # No timestamp and no context: cannot prove recency, so do not report it.
        return False
    age = (_dt_now() - ts).total_seconds() / 3600.0
    return age <= max_age_hours


def _parse_line_timestamp(line: str) -> Optional[datetime.datetime]:
    """Parse a leading ``YYYY-MM-DD HH:MM:SS`` from a log line, else None."""
    import datetime as _dt
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", line.strip())
    if not m:
        return None
    try:
        return _dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _dt_now():
    import datetime as _dt
    return _dt.datetime.now()


def _recency_filter_lines(text: str, max_age_hours: Optional[float]) -> str:
    """Drop echo lines and stale lines, giving continuation lines their owner's timestamp.

    Walks the log in order, remembering the most recent dated header. A line without its
    own timestamp inherits that header's recency, so a traceback body is judged by when
    its error actually occurred rather than being kept forever.
    """
    import datetime as _dt
    keep = []
    current_ts: "Optional[_dt.datetime]" = None
    for line in text.splitlines():
        ts = _parse_line_timestamp(line)
        if ts is not None:
            current_ts = ts
        if _is_echo_line(line):
            continue
        if max_age_hours and max_age_hours > 0:
            if current_ts is None:
                continue
            if (_dt.datetime.now() - current_ts).total_seconds() / 3600.0 > max_age_hours:
                continue
        keep.append(line)
    return "\n".join(keep)


def scan_text(text: str, fingerprints: Optional[List[Dict]] = None,
              max_age_hours: Optional[float] = None) -> ScanResult:
    """Scan text against all fingerprints. Returns matched issues.

    Evidence is the specific matching line, not the head of the blob — otherwise every
    fingerprint in a multi-match scan reports the same first-500-chars and the report is
    unactionable. Lines that merely *quote* a pattern (custodian's own reports) are skipped
    so a repaired issue does not resurrect itself on the next pass. ``max_age_hours`` drops
    stale matches — including continuation lines, which inherit the timestamp of the dated
    header above them — so an already-seen old error stops reporting as a live problem.
    """
    result = ScanResult()
    if fingerprints is None:
        fingerprints = ALL_FINGERPRINTS

    # Pre-filter echo + stale lines once; every fingerprint matches against clean text.
    clean = _recency_filter_lines(text, max_age_hours)
    if not clean:
        return result

    for fp in fingerprints:
        matched = match_fingerprint(clean, fp)
        if matched:
            evidence = _first_matching_line(clean, matched) or ""
            result.add_issue(
                fingerprint_id=fp["id"],
                source=fp.get("source", "unknown"),
                evidence=evidence[:500],
                tier=fp.get("tier", 3),
                auto_fix=fp.get("auto_fix"),
            )
    return result


def scan_files(file_paths: List[Path], fingerprints: Optional[List[Dict]] = None,
               max_age_hours: Optional[float] = None) -> ScanResult:
    """Scan multiple files against fingerprints. Concatenates results.

    Files inside custodian's own storage/journal/output trees are skipped: re-reading them
    turns previous findings into fresh evidence (the stale-echo bug). ``max_age_hours``
    drops stale matches so a resolved error stops re-reporting every cycle.
    """
    combined = ScanResult()
    for path in file_paths:
        try:
            if not path.exists():
                combined.errors.append(f"File not found: {path}")
                continue
            if is_echo_path(path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            result = scan_text(text, fingerprints, max_age_hours=max_age_hours)
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
