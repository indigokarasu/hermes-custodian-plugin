"""Cron job health monitor — parse jobs.json, categorize errors, auto-remediate, alert.

Runs as both:
  - A registered tool (custodian_cron_health) for on-demand checks
  - A cron job (custodian:cron-health) for scheduled monitoring

Data sources:
  - $HERMES_HOME/cron/jobs.json (job metadata + status)
  - $HERMES_HOME/cron/output/<job_id>/<timestamp>.md (run output)
  - $HERMES_HOME/logs/errors.log (root-cause errors)
  - $HERMES_HOME/logs/agent.log (agent-level logs)

Error categories (matched against last_error + logs):
  - google-workspace-mcp-unavailable — MCP server not running / not registered
  - google-auth — invalid_grant / 401 / token expired or revoked
  - rate-limit — HTTP 429 from LLM provider or API
  - execute-code-blocked — execute_code called in cron (no approver)
  - missing-script-tool — script file not found or tool not available
  - timeout — idle timeout or upstream timeout
  - oom — out of memory / jemalloc / allocation failure
  - unknown — uncategorized (surface for human review)

Auto-remediation:
  - google-workspace-mcp-unavailable → attempt MCP reconnect via gateway restart
  - failure_streak >= AUTO_PAUSE_THRESHOLD → pause job + report
  - execute-code-blocked → flag as "needs redesign" (can't auto-fix)

Alerting:
  - failure_streak >= 3 → immediate alert
  - healthy job newly flips to error → immediate alert
  - error_count > ERROR_COUNT_THRESHOLD → summary alert
  - daily health line for briefing
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    """Resolve HERMES_HOME — must use env var, never __file__."""
    home = os.environ.get("HERMES_HOME")
    if not home:
        home = os.path.join(os.path.expanduser("~"), ".hermes")
    return Path(home)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _resolve_alert_state() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    state_dir = Path(home) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "custodian-cron-alerts.json"


def _load_alert_state() -> Dict[str, Any]:
    """Last-alerted streak per job, so a standing error is reported once — not every run."""
    try:
        with open(_resolve_alert_state(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_alert_state(state: Dict[str, Any]) -> None:
    try:
        path = _resolve_alert_state()
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=0, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("could not persist alert state: %s", exc)


def _resolve_jobs_json() -> Path:
    return _get_hermes_home() / "cron" / "jobs.json"


def _resolve_cron_output_dir() -> Path:
    return _get_hermes_home() / "cron" / "output"


def _resolve_errors_log() -> Path:
    return _get_hermes_home() / "logs" / "errors.log"


def _resolve_agent_log() -> Path:
    return _get_hermes_home() / "logs" / "agent.log"

# Alert thresholds
#
# CONSECUTIVE_FAILURES_ALERT = 1: alert on the FIRST failure of any job. It was 3,
# which meant a daily job could fail three nights running before anyone heard —
# and combined with the recovery-state bug below, a job that failed, recovered,
# and failed again was silent forever. Detection that waits for a third failure is
# not monitoring. `failure_streak <= 1` (a just-flipped job) is now redundant but
# kept for clarity.
CONSECUTIVE_FAILURES_ALERT = 1
AUTO_PAUSE_THRESHOLD = 5
ERROR_COUNT_THRESHOLD = 10  # total error jobs before summary alert

# How many recent error lines to scan for root-cause categorization
ERROR_LOG_TAIL = 200


# ---------------------------------------------------------------------------
# Error category definitions
# ---------------------------------------------------------------------------

ERROR_CATEGORIES: List[Dict[str, Any]] = [
    {
        "id": "google-workspace-mcp-unavailable",
        "description": "Google Workspace MCP server not running or not registered",
        "patterns": [
            r"google.*workspace.*mcp.*(unavailable|not.*running|not.*registered|not.*found)",
            r"mcp.*server.*(not.*responding|not.*available|not.*connected)",
            r"failed to parse JSONRPC message from server",
            r"Connection refused.*mcp",
            r"mcp.*tool.*not.*available",
        ],
        "auto_fix": "restart_gateway_and_reconnect_mcp",
        "auto_fixable": True,
    },
    {
        "id": "google-auth",
        "description": "Google OAuth token expired, revoked, or invalid",
        "patterns": [
            r"invalid_grant",
            r"token has been expired or revoked",
            r"gmail\.api.*401",
            r"401.*google",
            r"unauthorized.*google",
            r"refresh.*token.*(expired|revoked|invalid)",
            r"re-authorization needed",
        ],
        "auto_fix": "reauthorize_google_oauth",
        "auto_fixable": True,
    },
    {
        "id": "rate-limit",
        "description": "HTTP 429 rate limit from LLM provider or API",
        "patterns": [
            r"HTTP 429",
            r"429.*rate.limit",
            r"too many concurrent requests",
            r"temporarily rate-limited",
            r"Provider returned error.*429",
        ],
        "auto_fix": "stagger_cron_schedules",
        "auto_fixable": False,  # requires human decision on which jobs to stagger
    },
    {
        "id": "execute-code-blocked",
        "description": "execute_code called in cron context (no approver available)",
        "patterns": [
            r"execute_code.*blocked",
            r"execute_code.*not.*allowed",
            r"cron_mode.*deny",
            r"approval_pending.*execute_code",
        ],
        "auto_flag": "needs_redesign",
        "auto_fixable": False,
    },
    {
        "id": "missing-script-tool",
        "description": "Script file not found or required tool not available",
        "patterns": [
            r"No such file.*script",
            r"script.*not found",
            r"cannot execute.*script",
            r"tool.*not.*available",
            r"ModuleNotFoundError",
            r"command not found",
        ],
        "auto_fix": None,
        "auto_fixable": False,
    },
    {
        "id": "timeout",
        "description": "Idle timeout or upstream timeout exceeded",
        "patterns": [
            r"idle for.*limit.*s",
            r"TimeoutError",
            r"timed out after",
            r"upstream idle timeout",
            r"Response remained truncated",
        ],
        "auto_fix": None,
        "auto_fixable": False,
    },
    {
        "id": "oom",
        "description": "Out of memory or allocation failure",
        "patterns": [
            r"out of memory",
            r"OOM",
            r"jemalloc",
            r"cannot allocate",
            r"memory.*exhausted",
            r"allocation failure",
        ],
        "auto_fix": None,
        "auto_fixable": False,
    },
]


# ---------------------------------------------------------------------------
# Core: parse jobs.json
# ---------------------------------------------------------------------------

def load_jobs() -> List[Dict[str, Any]]:
    """Load jobs from jobs.json. Returns empty list on failure."""
    jobs_path = _resolve_jobs_json()
    if not jobs_path.exists():
        logger.warning("jobs.json not found at %s", jobs_path)
        return []
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8"))
        return data.get("jobs", [])
    except Exception as e:
        logger.error("Failed to parse jobs.json: %s", e)
        return []


def categorize_job_health(jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Categorize all jobs by health status.

    Returns:
        {
            "total": int,
            "ok": int,
            "error": int,
            "paused": int,
            "error_jobs": [...],  # jobs with last_status=error
            "chronic_jobs": [...],  # failure_streak >= 3
            "categories": {category_id: [job_names...]},
        }
    """
    result = {
        "total": len(jobs),
        "ok": 0,
        "error": 0,
        "paused": 0,
        "error_jobs": [],
        "chronic_jobs": [],
        "categories": {},
    }

    for job in jobs:
        status = job.get("last_status", "unknown")
        enabled = job.get("enabled", True)
        paused_at = job.get("paused_at")
        failure_streak = job.get("failure_streak", 0)

        if paused_at:
            result["paused"] += 1
        elif status == "ok":
            result["ok"] += 1
        elif status == "error":
            result["error"] += 1
            result["error_jobs"].append(job)

            if failure_streak >= CONSECUTIVE_FAILURES_ALERT:
                result["chronic_jobs"].append(job)

            # Categorize the error
            category = categorize_error(job)
            cat_id = category["id"]
            if cat_id not in result["categories"]:
                result["categories"][cat_id] = []
            result["categories"][cat_id].append(job.get("name", job.get("id", "?")))

    return result


# ---------------------------------------------------------------------------
# Error categorization
# ---------------------------------------------------------------------------

def categorize_error(job: Dict[str, Any]) -> Dict[str, Any]:
    """Categorize a job's error from last_error + recent logs.

    Returns the matching ERROR_CATEGORIES entry, or an "unknown" placeholder.
    """
    last_error = job.get("last_error", "") or ""
    job_name = job.get("name", "")
    job_id = job.get("id", "")

    # Gather evidence: last_error + recent log lines mentioning this job
    evidence = last_error
    recent_log_snippets = _find_recent_error_lines(job_name, job_id)
    if recent_log_snippets:
        evidence += "\n" + "\n".join(recent_log_snippets)

    if not evidence.strip():
        return {"id": "unknown", "description": "No error evidence available", "patterns": [], "auto_fix": None, "auto_fixable": False}

    # Try each category in order (first match wins)
    for cat in ERROR_CATEGORIES:
        for pattern in cat["patterns"]:
            try:
                if re.search(pattern, evidence, re.IGNORECASE):
                    return cat
            except re.error:
                continue

    return {"id": "unknown", "description": f"Uncategorized error: {last_error[:200]}", "patterns": [], "auto_fix": None, "auto_fixable": False}


def _find_recent_error_lines(job_name: str, job_id: str, tail: int = ERROR_LOG_TAIL) -> List[str]:
    """Find recent error log lines mentioning this job."""
    lines = []

    # Search errors.log
    for log_path in [_resolve_errors_log(), _resolve_agent_log()]:
        if not log_path.exists():
            continue
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            recent = text.splitlines()[-tail:]
            for line in recent:
                # Match by job name or job id
                if job_name and job_name.lower() in line.lower():
                    lines.append(f"[{log_path.name}] {line.strip()}")
                elif job_id and job_id in line:
                    lines.append(f"[{log_path.name}] {line.strip()}")
        except Exception:
            continue

    # Limit to 5 most recent matches
    return lines[-5:]


# ---------------------------------------------------------------------------
# Auto-remediation
# ---------------------------------------------------------------------------

def attempt_auto_remediation(job: Dict[str, Any], category: Dict[str, Any]) -> Dict[str, Any]:
    """Attempt safe auto-remediation for a failing job.

    Returns: {"action": str, "result": str, "success": bool}
    """
    cat_id = category.get("id", "unknown")
    job_name = job.get("name", "?")
    job_id = job.get("id", "?")
    failure_streak = job.get("failure_streak", 0)

    # Auto-pause chronically failing jobs
    if failure_streak >= AUTO_PAUSE_THRESHOLD:
        return _auto_pause_job(job, category)

    # Category-specific remediation
    if cat_id == "google-workspace-mcp-unavailable":
        return _fix_mcp_unavailable(job, category)

    if cat_id == "google-auth":
        return _fix_google_auth(job, category)

    if cat_id == "execute-code-blocked":
        return {
            "action": "flag_execute_code_blocked",
            "result": f"Job '{job_name}' uses execute_code in cron context — needs redesign (no approver under cron_mode:deny)",
            "success": False,
        }

    if cat_id == "rate-limit":
        return {
            "action": "flag_rate_limit",
            "result": f"Job '{job_name}' hit rate limits — needs schedule staggering or max_parallel reduction",
            "success": False,
        }

    return {
        "action": "none",
        "result": f"No auto-remediation available for category '{cat_id}' on job '{job_name}'",
        "success": False,
    }


def _auto_pause_job(job: Dict[str, Any], category: Dict[str, Any]) -> Dict[str, Any]:
    """Pause a chronically failing job to stop cycle burning."""
    job_id = job.get("id", "?")
    job_name = job.get("name", "?")
    failure_streak = job.get("failure_streak", 0)
    cat_desc = category.get("description", "unknown cause")

    reason = f"auto-paused: {failure_streak} consecutive failures ({cat_desc})"

    # We can't directly modify jobs.json from here (it's managed by the scheduler).
    # Return the action for the caller to execute via cronjob tool.
    return {
        "action": "auto_pause",
        "result": f"Job '{job_name}' hit {failure_streak} consecutive failures. Recommend: cronjob(action='pause', job_id='{job_id}'). Reason: {reason}",
        "success": True,  # the recommendation is actionable
        "job_id": job_id,
        "pause_reason": reason,
    }


def _fix_mcp_unavailable(job: Dict[str, Any], category: Dict[str, Any]) -> Dict[str, Any]:
    """Attempt to fix Google Workspace MCP unavailability.

    Strategy: check if gateway is running, restart if needed, then
    attempt MCP reconnection.
    """
    job_name = job.get("name", "?")

    # Check gateway health
    try:
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", "hermes-gateway"],
            capture_output=True, text=True, timeout=5
        )
        gateway_running = result.returncode == 0
    except Exception:
        gateway_running = False

    if not gateway_running:
        return {
            "action": "restart_gateway",
            "result": f"Gateway not running — restart required before MCP reconnect for job '{job_name}'",
            "success": False,  # needs human or higher-level automation
        }

    return {
        "action": "reconnect_mcp",
        "result": f"Gateway running but Google Workspace MCP unavailable for job '{job_name}' — attempt MCP reconnect",
        "success": False,  # MCP reconnection requires gateway restart which needs human
    }


def _fix_google_auth(job: Dict[str, Any], category: Dict[str, Any]) -> Dict[str, Any]:
    """Flag Google auth issue — requires user re-authorization."""
    job_name = job.get("name", "?")
    return {
        "action": "reauthorize_google",
        "result": f"Job '{job_name}' has Google auth failure — user must re-authorize OAuth tokens",
        "success": False,
    }


# ---------------------------------------------------------------------------
# Main entry point: full cron health check
# ---------------------------------------------------------------------------

def run_cron_health_check(dry_run: bool = False) -> Dict[str, Any]:
    """Run a full cron health check.

    Returns a structured report:
        {
            "timestamp": str,
            "total": int, "ok": int, "error": int, "paused": int,
            "error_rate": float,
            "alerts": [...],          # jobs needing immediate attention
            "chronic_jobs": [...],     # failure_streak >= 3
            "categories": {...},       # error category → [job_names]
            "auto_remediations": [...], # attempted fixes
            "daily_health_line": str,  # one-liner for briefing
        }
    """
    jobs = load_jobs()
    if not jobs:
        return {"error": "No jobs found", "total": 0}

    health = categorize_job_health(jobs)
    error_rate = health["error"] / max(health["total"], 1)

    alerts = []
    auto_remediations = []
    alerted_this_pass = False
    alert_state = _load_alert_state()

    # Re-arm every job that is NOT currently failing.
    #
    # The loop below walks only `health["error_jobs"]`, so a job's state entry was
    # NEVER cleared when it recovered. Consequence: fail (state=1) -> recover ->
    # fail again (streak=1) meant `failure_streak != last_streak` was `1 != 1` and
    # the second, genuinely NEW outage was silent — and stayed silent for every
    # later failure, because the stored baseline never moved. A job that fails
    # intermittently would alert ONCE EVER. Dropping the threshold alone does not
    # fix this; both changes are required.
    #
    # Recording 0 on recovery is what keeps the edge-trigger honest: the next flip
    # to error is a change from 0, so it reports once, as intended.
    if not dry_run:
        failing_ids = {j.get("id") for j in health["error_jobs"]}
        for job in jobs:
            if job.get("id") in failing_ids:
                continue
            state_key = f"{job.get('id')}::{job.get('name', '?')}"
            if alert_state.get(state_key) not in (None, 0):
                alert_state[state_key] = 0
                alerted_this_pass = True

    # Check each error job
    for job in health["error_jobs"]:
        category = categorize_error(job)
        failure_streak = job.get("failure_streak", 0)
        job_name = job.get("name", "?")
        last_error = (job.get("last_error") or "")[:200]

        # Build alert
        alert = {
            "job_name": job_name,
            "job_id": job.get("id", "?"),
            "category": category["id"],
            "category_description": category["description"],
            "failure_streak": failure_streak,
            "last_error": last_error,
            "auto_fixable": category.get("auto_fixable", False),
        }

        # Determine if this needs immediate notification.
        #
        # Edge-triggered, NOT level-triggered, and the state is a REPORTED FLAG
        # (0 = not reported / recovered, >=1 = this outage already reported) rather
        # than the raw streak.
        #
        # Keying on the raw streak is wrong twice over. With
        # CONSECUTIVE_FAILURES_ALERT = 1 the old `is_chronic` branch
        # (`streak >= threshold`) was true for EVERY streak value, so a standing
        # failure re-alerted on every pass; and even comparing
        # `failure_streak != last_streak` re-fires as the streak climbs 1->2->3 for
        # what is the SAME outage. Record "reported" instead, and let the re-arm pass
        # above reset it to 0 on recovery so the next flip alerts exactly once.
        state_key = f"{alert.get('job_id')}::{job_name}"
        already_reported = bool(alert_state.get(state_key, 0))

        if not already_reported and failure_streak >= 1:
            alerts.append(alert)
            if not dry_run:
                alert_state[state_key] = 1
            alerted_this_pass = True

        # Attempt auto-remediation
        if not dry_run:
            remediation = attempt_auto_remediation(job, category)
            if remediation["action"] != "none":
                auto_remediations.append({
                    "job_name": job_name,
                    **remediation,
                })

    if not dry_run and alerted_this_pass:
        _save_alert_state(alert_state)

    # Build daily health line
    daily_line = (
        f"Cron health: {health['ok']}/{health['total']} ok, "
        f"{health['error']} error, {health['paused']} paused"
    )
    if health["error"] > 0:
        offenders = []
        for cat_id, job_names in health["categories"].items():
            cat_desc = next((c["description"] for c in ERROR_CATEGORIES if c["id"] == cat_id), cat_id)
            offenders.append(f"{cat_desc}: {', '.join(job_names)}")
        daily_line += " | " + "; ".join(offenders)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": health["total"],
        "ok": health["ok"],
        "error": health["error"],
        "paused": health["paused"],
        "error_rate": round(error_rate, 4),
        "alerts": alerts,
        "chronic_jobs": [j.get("name", "?") for j in health["chronic_jobs"]],
        "categories": health["categories"],
        "auto_remediations": auto_remediations,
        "daily_health_line": daily_line,
    }


# ---------------------------------------------------------------------------
# Formatting for human-readable output
# ---------------------------------------------------------------------------

def format_health_report(report: Dict[str, Any]) -> str:
    """Format a health report as human-readable text."""
    lines = []
    lines.append(f"Cron Health Report — {report['timestamp']}")
    lines.append(f"{'='*60}")
    lines.append(f"Total: {report['total']} | Ok: {report['ok']} | Error: {report['error']} | Paused: {report['paused']}")
    lines.append(f"Error rate: {report['error_rate']:.1%}")
    lines.append("")

    if report["alerts"]:
        lines.append(f"ALERTS ({len(report['alerts'])}):")
        for alert in report["alerts"]:
            lines.append(f"  [{alert['category']}] {alert['job_name']}")
            lines.append(f"    Failures: {alert['failure_streak']} | Auto-fixable: {alert['auto_fixable']}")
            lines.append(f"    Error: {alert['last_error'][:120]}")
            lines.append("")

    if report["auto_remediations"]:
        lines.append(f"AUTO-REMEDIATION ATTEMPTS ({len(report['auto_remediations'])}):")
        for rem in report["auto_remediations"]:
            lines.append(f"  {rem['job_name']}: {rem['action']} — {rem['result']}")
        lines.append("")

    if report["categories"]:
        lines.append("ERROR CATEGORIES:")
        for cat_id, job_names in report["categories"].items():
            lines.append(f"  {cat_id}: {', '.join(job_names)}")
        lines.append("")

    lines.append(f"DAILY: {report['daily_health_line']}")

    return "\n".join(lines)
