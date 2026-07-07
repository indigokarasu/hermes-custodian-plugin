"""Custodian dashboard plugin — backend API routes.

Mounted at /api/plugins/custodian/ by the dashboard plugin system.
Provides endpoints for the grid-based dashboard UI:
  - status overview (health, issues, system counts)
  - scan history with results
  - issue list with escalation context
  - trigger scan
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, HTTPException

log = logging.getLogger(__name__)

router = APIRouter()


def _get_hermes_home() -> Path:
    home = os.environ.get("HERMES_HOME")
    if not home:
        home = os.path.join(os.path.expanduser("~"), ".hermes")
    return Path(home)


def _get_storage_dir() -> Path:
    return _get_hermes_home() / "commons" / "data" / "ocas-custodian"


def _get_journal_dir() -> Path:
    return _get_hermes_home() / "commons" / "journals" / "ocas-custodian"


def _load_jsonl_safe(path: Path) -> List[Dict]:
    records = []
    if not path.exists():
        return records
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception as e:
        log.warning("Error reading %s: %s", path, e)
    return records


def _scan_journal_dir(days: int = 7, limit: int = 20) -> List[Dict]:
    """Read recent journal entries for scan history."""
    journal_dir = _get_journal_dir()
    if not journal_dir.exists():
        return []

    all_entries = []
    date_dirs = sorted(
        [d for d in journal_dir.iterdir() if d.is_dir()], reverse=True
    )
    for date_dir in date_dirs[:days]:
        for jf in sorted(date_dir.iterdir(), reverse=True):
            if jf.suffix != ".json":
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                summary = _summarize_journal(data)
                all_entries.append(summary)
            except Exception:
                continue
        if len(all_entries) >= limit:
            break

    return all_entries[:limit]


def _summarize_journal(data: Dict) -> Dict:
    """Produce a compact scan history entry from a journal file."""
    entries = data.get("entries", [])
    observations = [e for e in entries if e.get("kind") == "observation"]
    actions = [e for e in entries if e.get("kind") == "action"]
    escalations = [e for e in entries if e.get("kind") == "escalation"]
    return {
        "run_id": data.get("run_id", ""),
        "created_at": data.get("created_at", ""),
        "issues_found": len(observations),
        "fixes_applied": len(actions),
        "escalations": len(escalations),
        "has_actions": data.get("has_actions", False),
        "has_escalations": data.get("has_escalations", False),
    }


def _compute_health(open_count: int, escalated_count: int, last_scan_age_min: Optional[int]) -> Dict:
    """Compute health state for the dashboard."""
    if open_count == 0:
        return {
            "state": "clear",
            "label": "All Clear",
            "detail": "Custodian is handling everything autonomously.",
            "icon": "shield-check",
        }
    elif escalated_count > 0:
        return {
            "state": "attention",
            "label": f"{escalated_count} need{'s' if escalated_count == 1 else ''} attention",
            "detail": f"{open_count} open issue{'s' if open_count != 1 else ''}, {escalated_count} require{'s' if escalated_count == 1 else ''} manual action.",
            "icon": "alert-triangle",
        }
    else:
        return {
            "state": "attention",
            "label": f"{open_count} open",
            "detail": f"{open_count} issue{'s' if open_count != 1 else ''} detected. Auto-fix may resolve them.",
            "icon": "alert-triangle",
        }


# ---------------------------------------------------------------------------
# GET /status — full dashboard data
# ---------------------------------------------------------------------------

@router.get("/status")
def get_status():
    """Return complete dashboard data: health, issues, system, scans."""
    storage_dir = _get_storage_dir()
    issues_path = storage_dir / "issues.jsonl"
    fixes_path = storage_dir / "fixes.jsonl"

    # Issues
    open_issues = []
    resolved_today = 0
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    escalated_issues = []

    if issues_path.exists():
        for line in issues_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                issue = json.loads(line)
                status = issue.get("status", "open")
                tier = issue.get("tier", 3)
                resolved_at = issue.get("resolved_at", "")

                if status == "resolved":
                    if resolved_at.startswith(today_str):
                        resolved_today += 1
                else:
                    open_issues.append(issue)
                    if tier >= 3:
                        escalated_issues.append(issue)
            except json.JSONDecodeError:
                continue

    # Last scan time
    journal_dir = _get_journal_dir()
    last_scan_at = None
    last_scan_age_min = None
    if journal_dir.exists():
        date_dirs = sorted(
            [d for d in journal_dir.iterdir() if d.is_dir()], reverse=True
        )
        if date_dirs:
            files = sorted(date_dirs[0].iterdir(), reverse=True)
            if files:
                try:
                    data = json.loads(files[0].read_text(encoding="utf-8"))
                    last_scan_at = data.get("created_at")
                    if last_scan_at:
                        try:
                            scan_time = datetime.fromisoformat(last_scan_at.replace("Z", "+00:00"))
                            delta = datetime.now(timezone.utc) - scan_time
                            last_scan_age_min = int(delta.total_seconds() / 60)
                        except Exception:
                            pass
                except Exception:
                    pass

    # Health
    health = _compute_health(len(open_issues), len(escalated_issues), last_scan_age_min)

    # System counts
    system = _get_system_counts()

    # Scan history (last 10)
    scan_history = _scan_journal_dir(days=7, limit=10)

    return {
        "plugin": "custodian",
        "version": "3.0.0",
        "health": health,
        "last_scan": {
            "at": last_scan_at,
            "age_minutes": last_scan_age_min,
        },
        "issues": {
            "open": len(open_issues),
            "escalated": len(escalated_issues),
            "resolved_today": resolved_today,
        },
        "escalations": escalated_issues,
        "system": system,
        "scan_history": scan_history,
    }


def _get_system_counts() -> Dict[str, Any]:
    """Gather system counts for the dashboard."""
    hermes_home = _get_hermes_home()

    # Cron jobs — try to read from jobs.json
    cron_total = 0
    cron_disabled = 0
    cron_never_run = 0
    jobs_path = hermes_home / "cron" / "jobs.json"
    if jobs_path.exists():
        try:
            jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
            if isinstance(jobs, list):
                cron_total = len(jobs)
                for j in jobs:
                    if not j.get("enabled", True):
                        cron_disabled += 1
                    if j.get("last_status") is None and j.get("last_run_at") is None:
                        cron_never_run += 1
            elif isinstance(jobs, dict):
                cron_total = len(jobs)
        except Exception:
            pass

    # Skills
    skills_active = 0
    skills_stale = 0
    skills_dir = hermes_home / "skills"
    journal_dir = hermes_home / "commons" / "journals"
    if skills_dir.exists():
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                skills_active += 1
                # Check for stale journal (no entry in 7+ days)
                if journal_dir.exists():
                    skill_journal = journal_dir / skill_dir.name
                    if skill_journal.exists():
                        try:
                            latest = max(
                                (d for d in skill_journal.iterdir() if d.is_dir()),
                                key=lambda d: d.name,
                                default=None,
                            )
                            if latest:
                                from datetime import timedelta
                                latest_date = datetime.strptime(latest.name, "%Y-%m-%d")
                                if datetime.now() - latest_date > timedelta(days=7):
                                    skills_stale += 1
                        except Exception:
                            pass

    # Gateway uptime — read from uptime file or estimate
    gateway_uptime = "unknown"
    uptime_path = hermes_home / "logs" / "gateway.log"
    if uptime_path.exists():
        try:
            stat = uptime_path.stat()
            age_hours = (datetime.now().timestamp() - stat.st_mtime) / 3600
            if age_hours < 1:
                gateway_uptime = f"{int(age_hours * 60)}m"
            elif age_hours < 48:
                gateway_uptime = f"{int(age_hours)}h"
            else:
                gateway_uptime = f"{int(age_hours / 24)}d"
        except Exception:
            pass

    return {
        "cron": {
            "total": cron_total,
            "disabled": cron_disabled,
            "never_run": cron_never_run,
        },
        "skills": {
            "active": skills_active,
            "stale": skills_stale,
        },
        "gateway": {
            "uptime": gateway_uptime,
        },
    }


# ---------------------------------------------------------------------------
# GET /scans — scan history
# ---------------------------------------------------------------------------

@router.get("/scans")
def get_scans(
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(20, ge=1, le=200),
):
    """Return recent scan history."""
    entries = _scan_journal_dir(days=days, limit=limit)
    return {"scans": entries, "count": len(entries)}


# ---------------------------------------------------------------------------
# GET /issues — list issues with optional filter
# ---------------------------------------------------------------------------

@router.get("/issues")
def get_issues(
    status: Optional[str] = Query(None, description="Filter by status: open, resolved"),
    tier: Optional[int] = Query(None, description="Filter by tier"),
    limit: int = Query(50, ge=1, le=500),
):
    """Return filtered issues."""
    storage_dir = _get_storage_dir()
    issues_path = storage_dir / "issues.jsonl"
    issues = _load_jsonl_safe(issues_path)

    if status:
        issues = [i for i in issues if i.get("status", "open") == status]
    if tier is not None:
        issues = [i for i in issues if i.get("tier", 0) == tier]

    total = len(issues)
    issues = issues[:limit]

    return {
        "issues": issues,
        "total": total,
        "limit": limit,
    }


# ---------------------------------------------------------------------------
# POST /scan — trigger a scan
# ---------------------------------------------------------------------------

@router.post("/scan")
def trigger_scan(mode: str = "light"):
    """Trigger a light scan. Returns scan results."""
    if mode not in ("light", "deep"):
        mode = "light"

    storage_dir = _get_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)

    from hermes_custodian_plugin.scanner import scan_text
    from hermes_custodian_plugin.journal import Journal

    journal = Journal()
    sources = []

    if mode == "light":
        # Scan gateway and error log tails
        for log_name in ["errors.log", "gateway.log"]:
            log_path = _get_hermes_home() / "logs" / log_name
            if log_path.exists():
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                    lines = text.splitlines()[-500:]
                    result = scan_text("\n".join(lines))
                    for issue in result.issues:
                        journal.add_observation(
                            fingerprint_id=issue["fingerprint_id"],
                            source=issue["source"],
                            evidence=issue["evidence"][:500],
                            tier=issue["tier"],
                        )
                    sources.append(str(log_path))
                except Exception as e:
                    log.warning("Error scanning %s: %s", log_path, e)

    journal_path = journal.write()
    entries = journal.get_entries()
    observations = [e for e in entries if e["kind"] == "observation"]

    return {
        "mode": mode,
        "sources_scanned": sources,
        "issues_found": len(observations),
        "journal": str(journal_path),
        "entries": entries[:20],
    }


# ---------------------------------------------------------------------------
# GET /confidence — confidence model scores
# ---------------------------------------------------------------------------

@router.get("/confidence")
def get_confidence():
    """Return confidence model scores for all tracked fingerprints."""
    storage_dir = _get_storage_dir()
    effectiveness_path = storage_dir / "fix_effectiveness.jsonl"
    records = _load_jsonl_safe(effectiveness_path)
    return {
        "fingerprints": records,
        "count": len(records),
    }
