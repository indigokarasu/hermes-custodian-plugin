"""Custodian dashboard plugin — backend API routes.

Mounted at /api/plugins/custodian/ by the dashboard plugin system.
Provides endpoints for status, issues, confidence model, and scan history.
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


# ---------------------------------------------------------------------------
# GET /status — plugin overview
# ---------------------------------------------------------------------------

@router.get("/status")
def get_status():
    """Return Custodian plugin status overview."""
    storage_dir = _get_storage_dir()
    issues_path = storage_dir / "issues.jsonl"
    effectiveness_path = storage_dir / "fix_effectiveness.jsonl"

    open_issues = 0
    resolved_issues = 0
    issues_by_tier: Dict[int, int] = {}
    if issues_path.exists():
        for line in issues_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                issue = json.loads(line)
                status = issue.get("status", "open")
                tier = issue.get("tier", 0)
                if status == "resolved":
                    resolved_issues += 1
                else:
                    open_issues += 1
                issues_by_tier[tier] = issues_by_tier.get(tier, 0) + 1
            except json.JSONDecodeError:
                continue

    fingerprints_tracked = 0
    autofix_eligible = 0
    if effectiveness_path.exists():
        effectiveness = _load_jsonl_safe(effectiveness_path)
        fingerprints_tracked = len(effectiveness)
        autofix_eligible = sum(
            1 for e in effectiveness
            if e.get("confidence_score", 0) >= 0.6 and e.get("recommended_tier", 3) == 1
        )

    # Last scan time
    journal_dir = _get_journal_dir()
    last_scan = None
    if journal_dir.exists():
        date_dirs = sorted(
            [d for d in journal_dir.iterdir() if d.is_dir()], reverse=True
        )
        if date_dirs:
            files = sorted(date_dirs[0].iterdir(), reverse=True)
            if files:
                try:
                    data = json.loads(files[0].read_text(encoding="utf-8"))
                    last_scan = data.get("created_at")
                except Exception:
                    pass

    return {
        "plugin": "custodian",
        "version": "2.0.0",
        "status": "active" if storage_dir.exists() else "not_initialized",
        "last_scan": last_scan,
        "issues": {
            "open": open_issues,
            "resolved": resolved_issues,
            "by_tier": issues_by_tier,
        },
        "confidence_model": {
            "fingerprints_tracked": fingerprints_tracked,
            "autofix_eligible": autofix_eligible,
        },
    }


# ---------------------------------------------------------------------------
# GET /issues — list issues
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

    # Filter
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


# ---------------------------------------------------------------------------
# GET /journals — scan journal history
# ---------------------------------------------------------------------------

@router.get("/journals")
def get_journals(
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(20, ge=1, le=200),
):
    """Return recent journal entries."""
    journal_dir = _get_journal_dir()
    if not journal_dir.exists():
        return {"journals": [], "count": 0}

    all_entries = []
    date_dirs = sorted(
        [d for d in journal_dir.iterdir() if d.is_dir()], reverse=True
    )

    for date_dir in date_dirs[:days]:
        for jf in sorted(date_dir.iterdir(), reverse=True):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                all_entries.append({
                    "run_id": data.get("run_id"),
                    "created_at": data.get("created_at"),
                    "entry_count": data.get("entry_count", 0),
                    "has_actions": data.get("has_actions", False),
                    "has_escalations": data.get("has_escalations", False),
                })
            except Exception:
                continue
        if len(all_entries) >= limit:
            break

    return {
        "journals": all_entries[:limit],
        "count": len(all_entries[:limit]),
    }
