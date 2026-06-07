"""Confidence model with auto tier promote/demote.

confidence_score = sample_confidence × success_rate
  sample_confidence = min(1.0, attempts / 5)
  success_rate = successes / (successes + failures)

RCA-enhanced: schedule_adjusted_stickiness, sub-fingerprint discrimination,
recurrence pattern weighting.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Recurrence pattern weights
PATTERN_WEIGHTS = {
    "A": 1.0,  # fix never applied
    "B": 0.0,  # fix didn't hold
    "C": 0.5,  # different cause
    "D": 0.0,  # transient (excluded)
    "E": 0.3,  # cascade
}


def _get_hermes_home() -> Path:
    home = os.environ.get("HERMES_HOME")
    if not home:
        home = os.path.join(os.path.expanduser("~"), ".hermes")
    return Path(home)


def get_storage_dir() -> Path:
    return _get_hermes_home() / "commons" / "data" / "ocas-custodian"


def _load_jsonl(path: Path) -> List[Dict]:
    """Load a JSONL file, skipping malformed lines."""
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
        logger.warning("Error reading %s: %s", path, e)
    return records


def _save_jsonl(path: Path, records: List[Dict]) -> None:
    """Write records to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


class ConfidenceModel:
    """Tracks fix outcomes per fingerprint and computes confidence scores."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or get_storage_dir()
        self._effectiveness: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _effectiveness_path(self) -> Path:
        return self.storage_dir / "fix_effectiveness.jsonl"

    def _fixes_path(self) -> Path:
        return self.storage_dir / "fixes.jsonl"

    def _load(self) -> None:
        """Load fix_effectiveness.jsonl, backfilling from fixes.jsonl if empty."""
        records = _load_jsonl(self._effectiveness_path())
        if records:
            for r in records:
                fp = r.get("fingerprint", "unknown")
                self._effectiveness[fp] = r
        else:
            self._backfill_from_fixes()

    def _backfill_from_fixes(self) -> None:
        """Backfill effectiveness from fixes.jsonl on first run."""
        fixes = _load_jsonl(self._fixes_path())
        effectiveness: Dict[str, Dict] = defaultdict(
            lambda: {"attempts": 0, "successes": 0, "failures": 0}
        )
        for fix in fixes:
            fp = fix.get("fingerprint", "unknown")
            e = effectiveness[fp]
            e["attempts"] += 1
            outcome = fix.get("outcome", "")
            if outcome in ("fix_applied", "applied", "success", "verified"):
                e["successes"] += 1
            elif outcome in ("fix_attempted_failed", "failed"):
                e["failures"] += 1

        for fp, e in effectiveness.items():
            self._effectiveness[fp] = self._compute_record(fp, e["attempts"], e["successes"], e["failures"])
        self._save()

    def _compute_record(self, fingerprint: str, attempts: int, successes: int,
                        failures: int, **extra) -> Dict[str, Any]:
        """Compute confidence record for a fingerprint."""
        sample_confidence = min(1.0, attempts / 5)
        total = successes + failures
        success_rate = successes / total if total > 0 else 0.0
        confidence_score = sample_confidence * success_rate

        # Determine recommended tier
        recommended_tier = 1  # default
        if attempts >= 2 and success_rate < 0.5:
            recommended_tier = 3
        elif attempts >= 3 and success_rate >= 0.85:
            recommended_tier = 1
        elif attempts == 0:
            recommended_tier = 3

        record = {
            "fingerprint": fingerprint,
            "attempts": attempts,
            "successes": successes,
            "failures": failures,
            "sample_confidence": round(sample_confidence, 4),
            "success_rate": round(success_rate, 4),
            "confidence_score": round(confidence_score, 4),
            "recommended_tier": recommended_tier,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        record.update(extra)
        return record

    def _save(self) -> None:
        """Persist effectiveness records to disk."""
        records = list(self._effectiveness.values())
        _save_jsonl(self._effectiveness_path(), records)

    def get_score(self, fingerprint: str) -> float:
        """Get confidence_score for a fingerprint."""
        rec = self._effectiveness.get(fingerprint)
        if rec:
            return rec.get("confidence_score", 0.0)
        return 0.0

    def get_recommended_tier(self, fingerprint: str) -> int:
        """Get recommended_tier for a fingerprint."""
        rec = self._effectiveness.get(fingerprint)
        if rec:
            return rec.get("recommended_tier", 3)
        return 3

    def record_outcome(self, fingerprint: str, success: bool) -> Dict[str, Any]:
        """Record a fix outcome and recompute confidence."""
        rec = self._effectiveness.get(fingerprint)
        if rec is None:
            rec = self._compute_record(fingerprint, 0, 0, 0)

        rec["attempts"] += 1
        if success:
            rec["successes"] += 1
        else:
            rec["failures"] += 1

        updated = self._compute_record(
            fingerprint, rec["attempts"], rec["successes"], rec["failures"]
        )
        # Preserve extra fields
        for k, v in rec.items():
            if k not in updated:
                updated[k] = v
        self._effectiveness[fingerprint] = updated
        self._save()
        return updated

    def should_autofix(self, fingerprint: str) -> bool:
        """Check if a fingerprint should be auto-fixed based on confidence."""
        score = self.get_score(fingerprint)
        tier = self.get_recommended_tier(fingerprint)
        return score >= 0.6 and tier == 1

    def should_escalate(self, fingerprint: str) -> bool:
        """Check if a fingerprint should be escalated based on confidence."""
        rec = self._effectiveness.get(fingerprint)
        if rec is None:
            return True  # unknown → escalate
        if rec["attempts"] >= 3 and rec.get("success_rate", 1.0) < 0.2:
            return True
        if rec["attempts"] >= 2 and rec.get("success_rate", 1.0) < 0.5:
            return True
        return False

    def get_all_scores(self) -> Dict[str, Dict[str, Any]]:
        """Return all fingerprint confidence records."""
        return dict(self._effectiveness)

    def get_summary(self) -> Dict[str, Any]:
        """Return a summary of the confidence model state."""
        total = len(self._effectiveness)
        autofix_eligible = sum(1 for fp in self._effectiveness if self.should_autofix(fp))
        escalate_eligible = sum(1 for fp in self._effectiveness if self.should_escalate(fp))
        return {
            "fingerprints_tracked": total,
            "autofix_eligible": autofix_eligible,
            "escalate_eligible": escalate_eligible,
            "records": self.get_all_scores(),
        }

    def compute_schedule_adjusted_stickiness(self, fingerprint: str,
                                               days_since_fix: float,
                                               avg_schedule_interval_days: float,
                                               recurrence_count: int) -> float:
        """Compute schedule-adjusted fix stickiness."""
        if avg_schedule_interval_days <= 0:
            return 0.0
        cycles_survived = days_since_fix / avg_schedule_interval_days
        return cycles_survived / (1 + recurrence_count)

    def check_fix_loop(self, fingerprint: str, fix_count: int,
                       stickiness_values: List[float]) -> bool:
        """Detect fix-loop: same fix applied >= 3 times with low stickiness."""
        if fix_count < 3:
            return False
        return all(s < 0.5 for s in stickiness_values)
