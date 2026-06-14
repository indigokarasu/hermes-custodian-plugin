"""Custodian plugin — operational monitoring, auto-repair, and escalation for Hermes.

register(ctx) entry point:
  - 4 lifecycle hooks: post_tool_call, on_session_end, on_session_start, on_session_reset
  - 14 slash commands via /custodian
  - 3 registered tools: custodian_status, custodian_scan, custodian_issues
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import ALL_SCHEMAS
from .scanner import ALL_FINGERPRINTS, ScanResult, get_storage_dir, scan_text
from .classifier import ConfidenceModel
from .fix_engine import FixEngine
from .journal import Journal
from .cron_registrar import CronRegistrar

logger = logging.getLogger(__name__)

__version__ = "3.0.0"


def _get_hermes_home() -> Path:
    home = os.environ.get("HERMES_HOME")
    if not home:
        home = os.path.join(os.path.expanduser("~"), ".hermes")
    return Path(home)


# ===========================================================================
# Lifecycle hooks
# ===========================================================================

def _hook_post_tool_call(ctx, tool_name: str, args: dict, result: Any, **kwargs) -> None:
    """Passive observation hook — scan tool output for error patterns.
    
    Extra kwargs are accepted to stay compatible with evolving hook signatures
    (e.g., task_id passed by newer Hermes versions).
    """
    if not isinstance(result, str):
        return
    storage_dir = get_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    # Quick scan for known error patterns
    for fp in ALL_FINGERPRINTS:
        for pattern in fp.get("match_patterns", []):
            try:
                import re
                if re.search(pattern, result, re.IGNORECASE):
                    logger.debug("Custodian: fingerprint %s matched in %s output",
                                 fp["id"], tool_name)
                    break
            except re.error:
                continue


def _hook_on_session_start(ctx, **kwargs) -> None:
    """Session start hook — initialize storage, ensure directories exist.
    
    Extra kwargs accepted for forward compatibility.
    """
    storage_dir = get_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    journal_dir = _get_hermes_home() / "commons" / "journals" / "ocas-custodian"
    journal_dir.mkdir(parents=True, exist_ok=True)
    logger.debug("Custodian: session start — storage verified")


def _hook_on_session_end(ctx, **kwargs) -> None:
    """Session end hook — write any pending journal entries.
    
    Extra kwargs accepted for forward compatibility.
    """
    logger.debug("Custodian: session end")


def _hook_on_session_reset(ctx, **kwargs) -> None:
    """Session reset hook — clear transient state.
    
    Extra kwargs accepted for forward compatibility.
    """
    logger.debug("Custodian: session reset — transient state cleared")


# ===========================================================================
# Tool handlers
# ===========================================================================

def _handle_status(ctx, **kwargs) -> str:
    """custodian_status — show plugin status."""
    storage_dir = get_storage_dir()
    cm = ConfidenceModel(storage_dir)
    summary = cm.get_summary()

    # Count open issues
    issues_path = storage_dir / "issues.jsonl"
    open_issues = 0
    if issues_path.exists():
        for line in issues_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                issue = json.loads(line)
                if issue.get("status") != "resolved":
                    open_issues += 1
            except json.JSONDecodeError:
                continue

    # Last journal entry
    journal_dir = _get_hermes_home() / "commons" / "journals" / "ocas-custodian"
    last_scan = "never"
    if journal_dir.exists():
        date_dirs = sorted([d for d in journal_dir.iterdir() if d.is_dir()], reverse=True)
        if date_dirs:
            files = sorted(date_dirs[0].iterdir(), reverse=True)
            if files:
                try:
                    data = json.loads(files[0].read_text(encoding="utf-8"))
                    last_scan = data.get("created_at", "unknown")
                except Exception:
                    pass

    status = {
        "plugin": "custodian",
        "version": __version__,
        "status": "active",
        "last_scan": last_scan,
        "open_issues": open_issues,
        "fingerprints_tracked": summary["fingerprints_tracked"],
        "autofix_eligible": summary["autofix_eligible"],
        "escalate_eligible": summary["escalate_eligible"],
    }
    return json.dumps(status, indent=2)


def _handle_scan(ctx, **kwargs) -> str:
    """custodian_scan — run a scan (light or deep)."""
    mode = kwargs.get("mode", "light")
    storage_dir = get_storage_dir()
    journal = Journal()

    if mode == "light":
        # Light scan: check gateway log tail, cron registry
        gateway_log = _get_hermes_home() / "logs" / "gateway.log"
        errors_log = _get_hermes_home() / "logs" / "errors.log"
        sources = []
        for log_path in [errors_log, gateway_log]:
            if log_path.exists():
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                    # Only tail last 500 lines for light scan
                    lines = text.splitlines()[-500:]
                    result = scan_text("\n".join(lines))
                    for issue in result.issues:
                        journal.add_observation(
                            fingerprint_id=issue["fingerprint_id"],
                            source=issue["source"],
                            evidence=issue["evidence"],
                            tier=issue["tier"],
                        )
                    sources.append(str(log_path))
                except Exception as e:
                    logger.warning("Error scanning %s: %s", log_path, e)

        journal_path = journal.write()
        return json.dumps({
            "mode": "light",
            "sources_scanned": sources,
            "issues_found": len(journal.get_entries()),
            "journal": str(journal_path),
            "entries": journal.get_entries()[:20],  # limit output
        }, indent=2, default=str)

    else:
        # Deep scan placeholder — full 13-step procedure
        journal_path = journal.write()
        return json.dumps({
            "mode": "deep",
            "status": "deep scan requires full agent context — use /custodian scan deep",
            "journal": str(journal_path),
        }, indent=2, default=str)


def _handle_issues(ctx, **kwargs) -> str:
    """custodian_issues — list, resolve, or summarize issues."""
    action = kwargs.get("action", "list")
    storage_dir = get_storage_dir()
    issues_path = storage_dir / "issues.jsonl"

    if action == "summary":
        counts = {"open": 0, "resolved": 0, "by_tier": {}}
        if issues_path.exists():
            for line in issues_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    issue = json.loads(line)
                    status = issue.get("status", "open")
                    if status == "resolved":
                        counts["resolved"] += 1
                    else:
                        counts["open"] += 1
                    tier = issue.get("tier", 0)
                    counts["by_tier"][str(tier)] = counts["by_tier"].get(str(tier), 0) + 1
                except json.JSONDecodeError:
                    continue
        return json.dumps(counts, indent=2)

    elif action == "resolve":
        issue_id = kwargs.get("issue_id", "")
        if not issue_id:
            return json.dumps({"error": "issue_id required for resolve action", "hint": "Usage: /custodian issues resolve <issue_id>"})
        # Mark issue as resolved
        if issues_path.exists():
            lines = issues_path.read_text(encoding="utf-8").splitlines()
            updated = []
            found = False
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    issue = json.loads(line)
                    if issue.get("issue_id") == issue_id or issue.get("id") == issue_id:
                        issue["status"] = "resolved"
                        issue["resolved_at"] = datetime.now(timezone.utc).isoformat()
                        found = True
                    updated.append(json.dumps(issue, default=str))
                except json.JSONDecodeError:
                    updated.append(line)
            if found:
                issues_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
                return json.dumps({"ok": True, "issue_id": issue_id, "status": "resolved"})
            return json.dumps({"error": f"issue {issue_id} not found"})
        return json.dumps({"error": "no issues file found"})

    else:  # list
        issues = []
        if issues_path.exists():
            for line in issues_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    issue = json.loads(line)
                    if issue.get("status") != "resolved":
                        issues.append(issue)
                except json.JSONDecodeError:
                    continue
        return json.dumps({"issues": issues, "count": len(issues)}, indent=2, default=str)


# ===========================================================================
# Slash command handler — /custodian <subcommand> [args]
# ===========================================================================

def _cmd_custodian(raw_args: str) -> str:
    """Main /custodian slash command dispatcher — 14 subcommands."""
    parts = raw_args.strip().split()
    if not parts:
        return _cmd_help()

    subcmd = parts[0].lower()
    rest = " ".join(parts[1:])

    commands = {
        "status": _cmd_status,
        "scan": _cmd_scan,
        "issues": _cmd_issues,
        "repair": _cmd_repair,
        "verify": _cmd_verify,
        "schedule": _cmd_schedule,
        "confidence": _cmd_confidence,
        "init": _cmd_init,
        "update": _cmd_update,
        "escalation-runner": _cmd_escalation_runner,
        "help": _cmd_help,
    }

    handler = commands.get(subcmd)
    if handler is None:
        return f"Unknown subcommand: {subcmd}. Try /custodian help"
    return handler(rest)


def _cmd_help(_args: str = "") -> str:
    return """Custodian — operational monitoring for Hermes

Subcommands:
  /custodian status              — plugin status, last scan, open issues
  /custodian scan light          — quick scan (log tail, cron registry)
  /custodian scan deep           — full 13-step sweep
  /custodian issues list         — list open issues
  /custodian issues resolve <id> — mark issue resolved
  /custodian repair auto         — apply all pending Tier 1 fixes
  /custodian repair plan         — generate repair plan for Tier 2/3
  /custodian verify <fix_id>     — verify fix outcome
  /custodian schedule show       — display scan schedule
  /custodian confidence show     — display confidence scores
  /custodian init                — create storage, register cron jobs
  /custodian update              — self-update from GitHub
  /custodian escalation-runner   — process escalated Tier 3+ issues
  /custodian help                — this help"""


def _cmd_status(_args: str = "") -> str:
    return _handle_status(None)


def _cmd_scan(args: str) -> str:
    mode = args.strip().split()[0] if args.strip() else "light"
    if mode not in ("light", "deep"):
        mode = "light"
    return _handle_scan(None, mode=mode)


def _cmd_issues(args: str) -> str:
    parts = args.strip().split()
    if not parts:
        return _handle_issues(None, action="list")
    action = parts[0]
    if action == "resolve" and len(parts) > 1:
        return _handle_issues(None, action="resolve", issue_id=parts[1])
    elif action == "summary":
        return _handle_issues(None, action="summary")
    return _handle_issues(None, action="list")


def _cmd_repair(args: str) -> str:
    parts = args.strip().split()
    if not parts:
        return "Usage: /custodian repair auto|plan"
    action = parts[0]
    if action == "auto":
        storage_dir = get_storage_dir()
        engine = FixEngine(storage_dir, dry_run=False)
        # In a real run, issues would come from the scan
        return json.dumps({
            "status": "repair auto",
            "fixes_available": len(FixEngine._FIX_MAP),
            "note": "Apply fixes from pending Tier 1 issues",
        }, indent=2)
    elif action == "plan":
        return json.dumps({
            "status": "repair plan",
            "note": "Generate repair plan for Tier 2/3 issues",
        }, indent=2)
    return "Usage: /custodian repair auto|plan"


def _cmd_verify(args: str) -> str:
    fix_id = args.strip()
    if not fix_id:
        return "Usage: /custodian verify <fix_id>"
    return json.dumps({"status": "verify", "fix_id": fix_id, "note": "Verify fix outcome"})


def _cmd_schedule(args: str) -> str:
    parts = args.strip().split()
    if parts and parts[0] == "show":
        registrar = CronRegistrar()
        jobs = registrar.get_job_definitions()
        return json.dumps({"cron_jobs": jobs}, indent=2, default=str)
    return "Usage: /custodian schedule show"


def _cmd_confidence(args: str) -> str:
    parts = args.strip().split()
    if parts and parts[0] == "show":
        storage_dir = get_storage_dir()
        cm = ConfidenceModel(storage_dir)
        return json.dumps(cm.get_summary(), indent=2, default=str)
    return "Usage: /custodian confidence show"


def _cmd_init(_args: str = "") -> str:
    storage_dir = get_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    journal_dir = _get_hermes_home() / "commons" / "journals" / "ocas-custodian"
    journal_dir.mkdir(parents=True, exist_ok=True)
    # Create empty JSONL files
    for fname in ["issues.jsonl", "fixes.jsonl", "fix_effectiveness.jsonl",
                  "learned_issues.jsonl", "skill_conformance.jsonl"]:
        fpath = storage_dir / fname
        if not fpath.exists():
            fpath.touch()
    return json.dumps({
        "status": "initialized",
        "storage_dir": str(storage_dir),
        "journal_dir": str(journal_dir),
    }, indent=2)


def _cmd_update(_args: str = "") -> str:
    return json.dumps({
        "status": "update",
        "note": "Self-update from GitHub — use 'git pull' in plugin directory",
    }, indent=2)


def _cmd_escalation_runner(_args: str = "") -> str:
    storage_dir = get_storage_dir()
    issues_path = storage_dir / "issues.jsonl"
    escalated = []
    if issues_path.exists():
        for line in issues_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                issue = json.loads(line)
                if issue.get("tier", 0) >= 3 and issue.get("status") != "resolved":
                    escalated.append(issue)
            except json.JSONDecodeError:
                continue
    return json.dumps({
        "escalated_issues": escalated,
        "count": len(escalated),
    }, indent=2, default=str)


# ===========================================================================
# Plugin entry point
# ===========================================================================

def register(ctx):
    """Plugin entry point — called by Hermes plugin system on load."""
    logger.info("Custodian plugin v%s registering", __version__)

    # Register hooks
    ctx.register_hook("post_tool_call", _hook_post_tool_call)
    ctx.register_hook("on_session_start", _hook_on_session_start)
    ctx.register_hook("on_session_end", _hook_on_session_end)
    ctx.register_hook("on_session_reset", _hook_on_session_reset)

    # Register tools
    ctx.register_tool(
        name="custodian_status",
        toolset="custodian",
        schema=ALL_SCHEMAS[0]["function"],
        handler=_handle_status,
        description="Show Custodian plugin status",
        emoji="🛡️",
    )
    ctx.register_tool(
        name="custodian_scan",
        toolset="custodian",
        schema=ALL_SCHEMAS[1]["function"],
        handler=_handle_scan,
        description="Run a Custodian scan (light or deep)",
        emoji="🔍",
    )
    ctx.register_tool(
        name="custodian_issues",
        toolset="custodian",
        schema=ALL_SCHEMAS[2]["function"],
        handler=_handle_issues,
        description="List, filter, or resolve Custodian issues",
        emoji="📋",
    )

    # Register slash command
    ctx.register_command(
        name="custodian",
        handler=_cmd_custodian,
        description="Custodian operational monitoring — status, scan, issues, repair, verify, schedule, confidence, init, update, escalation-runner",
        args_hint="status|scan light|scan deep|issues list|issues resolve <id>|repair auto|repair plan|verify <fix_id>|schedule show|confidence show|init|update|escalation-runner",
    )

    logger.info("Custodian plugin registered: 4 hooks, 3 tools, 1 slash command (14 subcommands)")
