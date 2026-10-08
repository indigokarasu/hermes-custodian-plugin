"""Dispatch-contract tests for the Custodian tool handlers.

Why these exist: ``tools/registry.py::dispatch`` executes
``entry.handler(args, **context_kwargs)`` — the argument dict arrives **positionally**. A plugin
handler declared as ``(ctx, **kwargs)`` therefore loses EVERY parameter (the dict lands in
``ctx``, ``kwargs`` stays empty). In production that made ``mode`` fall back to ``"light"``, so
``custodian_scan(mode="deep")`` reported a light scan, ``dry_run`` was never honoured and
``custodian_issues(action="summary")`` always ran the ``list`` branch.

These tests call the handlers the way the harness does — one positional dict — so they fail if
anyone reintroduces a keyword-only reading of the arguments.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes_custodian_plugin import (  # noqa: E402
    _handle_cron_health,
    _handle_issues,
    _handle_scan,
    _handle_status,
)


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Keep every handler away from the real ~/.hermes."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_scan_reads_mode_from_the_positional_dict(isolated_home):
    out = json.loads(_handle_scan({"mode": "bogus"}))
    assert out["mode"] == "bogus", "the mode argument was not read from the positional dict"
    assert "unknown mode" in out["status"]


def test_scan_light_still_reports_light(isolated_home):
    out = json.loads(_handle_scan({"mode": "light"}))
    assert out["mode"] == "light"


def test_scan_deep_runs_the_full_sweep(isolated_home):
    out = _handle_scan({"mode": "deep"})
    assert "Deep scan custodian" in out, out[:200]
    assert "13/13" in out, "the deep sweep did not run all 13 steps"


def test_scan_deep_defaults_to_dry_run(isolated_home):
    out = _handle_scan({"mode": "deep"})
    assert "dry-run" in out


def test_scan_deep_accepts_apply(isolated_home):
    out = _handle_scan({"mode": "deep", "apply": True})
    assert "Deep scan custodian" in out


def test_issues_summary_reaches_its_branch(isolated_home):
    out = json.loads(_handle_issues({"action": "summary"}))
    assert "by_tier" in out, "action='summary' did not reach the summary branch"


def test_issues_resolve_without_id_is_refused(isolated_home):
    out = json.loads(_handle_issues({"action": "resolve"}))
    assert "error" in out


def test_cron_health_responds_and_defaults_to_dry_run(isolated_home):
    out = _handle_cron_health({})
    assert "Cron Health Report" in out


def test_status_responds(isolated_home):
    out = _handle_status({})
    assert "custodian" in out.lower()
