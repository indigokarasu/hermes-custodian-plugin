"""Observation and action journal output.

Journals are written to {HERMES_HOME}/commons/journals/ocas-custodian/YYYY-MM-DD/{run_id}.json
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    home = os.environ.get("HERMES_HOME")
    if not home:
        home = os.path.join(os.path.expanduser("~"), ".hermes")
    return Path(home)


def get_journal_dir() -> Path:
    return _get_hermes_home() / "commons" / "journals" / "ocas-custodian"


class Journal:
    """Write observation and action journals."""

    def __init__(self, journal_dir: Optional[Path] = None, run_id: Optional[str] = None):
        self.journal_dir = journal_dir or get_journal_dir()
        self.run_id = run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self._entries: List[Dict[str, Any]] = []

    def add_observation(self, fingerprint_id: str, source: str, evidence: str,
                        tier: int) -> Dict[str, Any]:
        """Add an observation entry (detection without fix)."""
        entry = {
            "kind": "observation",
            "fingerprint_id": fingerprint_id,
            "source": source,
            "evidence": evidence[:1000],
            "tier": tier,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._entries.append(entry)
        return entry

    def add_action(self, fingerprint_id: str, fix_id: str, command: str,
                   outcome: str, reversibility: str = "") -> Dict[str, Any]:
        """Add an action entry (fix applied)."""
        entry = {
            "kind": "action",
            "fingerprint_id": fingerprint_id,
            "fix_id": fix_id,
            "command": command,
            "outcome": outcome,
            "reversibility": reversibility,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._entries.append(entry)
        return entry

    def add_escalation(self, issue_id: str, fingerprint_id: str,
                       briefing: str, pattern: str = "") -> Dict[str, Any]:
        """Add an escalation entry."""
        entry = {
            "kind": "escalation",
            "issue_id": issue_id,
            "fingerprint_id": fingerprint_id,
            "briefing": briefing,
            "pattern": pattern,
            "escalation_needed": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._entries.append(entry)
        return entry

    def get_entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    def has_actions(self) -> bool:
        return any(e["kind"] == "action" for e in self._entries)

    def has_escalations(self) -> bool:
        return any(e["kind"] == "escalation" for e in self._entries)

    def _journal_subdir(self) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        subdir = self.journal_dir / date_str
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir

    def write(self) -> Path:
        """Write all entries to a journal file. Returns the file path."""
        subdir = self._journal_subdir()
        journal_path = subdir / f"{self.run_id}.json"

        journal_data = {
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entry_count": len(self._entries),
            "has_actions": self.has_actions(),
            "has_escalations": self.has_escalations(),
            "entries": self._entries,
        }

        journal_path.write_text(json.dumps(journal_data, indent=2, default=str), encoding="utf-8")
        logger.info("Journal written: %s (%d entries)", journal_path, len(self._entries))
        return journal_path

    def to_dict(self) -> Dict[str, Any]:
        """Return the journal as a dict (for in-memory use)."""
        return {
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entry_count": len(self._entries),
            "has_actions": self.has_actions(),
            "has_escalations": self.has_escalations(),
            "entries": self._entries,
        }
