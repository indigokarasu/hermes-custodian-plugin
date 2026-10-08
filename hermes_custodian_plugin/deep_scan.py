#!/usr/bin/env python3
"""custodian.deep_scan — balayage complet de l'installation.

Remplace le placeholder de `_handle_scan` (branche `deep`) par un sweep REELLEMENT execute.

Contrat d'appel du harness (tools/registry.py::dispatch) :
    result = entry.handler(args, **context_kwargs)
Le dict d'arguments arrive donc en POSITIONNEL. La signature d'un handler de tool de plugin doit
etre `def handler(args, **kwargs)`, jamais `def handler(ctx, **kwargs)` — sinon `args` tombe dans
`ctx` et tous les parametres sont perdus (bug corrige le 08/10/2026 : `mode` retombait sur
"light", `dry_run` n'etait jamais honore).

Les 13 etapes sont listees dans STEPS et le rapport dit pour chacune : ok / warn / error / skipped.
Rien n'est applique sans `apply_fixes=True` : la reparation est en dry-run par defaut.
"""
from __future__ import annotations

import json
import os
import re as _re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- le plugin est charge par chemin de fichier : imports relatifs si possible, sinon directs ---
try:
    from .scanner import ALL_FINGERPRINTS, scan_files, get_storage_dir
    from .fix_engine import FixEngine
    from .classifier import ConfidenceModel
    from .journal import Journal
    from .cron_health import run_cron_health_check
except ImportError:  # pragma: no cover - fallback quand charge hors package
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scanner import ALL_FINGERPRINTS, scan_files, get_storage_dir  # type: ignore
    from fix_engine import FixEngine  # type: ignore
    from classifier import ConfidenceModel  # type: ignore
    from journal import Journal  # type: ignore
    from cron_health import run_cron_health_check  # type: ignore


STEPS: List[str] = [
    "inventaire des sources",
    "scan etendu des journaux (tous les logs recents)",
    "sante des cron jobs",
    "conformite des jobs (script et skill references existent)",
    "hygiene des donnees (JSONL volumineux, dossiers de travail)",
    "etat du coeur (gateway, proces vivant, desync process/disque)",
    "patchs locaux du coeur (registre field-notes)",
    "integrite des skills (nombre, index)",
    "issues connues non resolues",
    "modele de confiance des reparations",
    "reparation Tier 1 (dry-run par defaut)",
    "escalades (Tier 2 et au-dela)",
    "journal et rapport",
]


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _recent_logs(home: Path, days: int = 7, limit: int = 40) -> List[Path]:
    """Logs modifies dans les `days` derniers jours, les plus recents d'abord."""
    logdir = home / "logs"
    if not logdir.is_dir():
        return []
    cutoff = time.time() - days * 86400
    out = []
    for p in logdir.rglob("*.log"):
        try:
            if p.is_file() and p.stat().st_mtime >= cutoff:
                out.append(p)
        except OSError:
            continue
    out.sort(key=lambda p: -p.stat().st_mtime)
    return out[:limit]


def _jobs(home: Path) -> List[Dict[str, Any]]:
    p = home / "cron" / "jobs.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    jobs = raw if isinstance(raw, list) else raw.get("jobs", [])
    return jobs if isinstance(jobs, list) else []


def _gateway_alive() -> Dict[str, Any]:
    """Proces gateway vivant + sa date de demarrage (lue dans /proc, pas en parsant une date)."""
    out: Dict[str, Any] = {"alive": False, "pid": None, "started": None}
    procd = Path("/proc")
    if not procd.is_dir():
        return out
    for entry in procd.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if "gateway" in cmdline and "hermes" in cmdline:
            out["alive"] = True
            out["pid"] = int(entry.name)
            try:
                out["started"] = int(entry.stat().st_mtime)
            except OSError:
                pass
            break
    return out


def run_deep_scan(
    storage_dir: Optional[Path] = None,
    apply_fixes: bool = False,
    max_age_hours: int = 168,
    logger=None,
) -> Dict[str, Any]:
    """Execute les 13 etapes et renvoie le rapport structure (jamais d'exception remontee)."""
    home = _home()
    storage_dir = Path(storage_dir) if storage_dir else get_storage_dir()
    journal = Journal()
    steps: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    t0 = time.time()

    def step(name: str, status: str, detail: str = "", findings: Optional[List[Any]] = None) -> None:
        steps.append({
            "step": len(steps) + 1,
            "name": name,
            "status": status,
            "detail": detail,
            "findings": (findings or [])[:25],
        })

    # 1 ------------------------------------------------------------------
    logs = _recent_logs(home, days=max(1, max_age_hours // 24))
    jobs = _jobs(home)
    step("inventaire des sources", "ok",
         f"{len(logs)} journaux recents, {len(jobs)} cron jobs, hermes_home={home}")

    # 2 ------------------------------------------------------------------
    try:
        res = scan_files(logs, max_age_hours=max_age_hours) if logs else None
        found = list(getattr(res, "issues", []) or []) if res else []
        for it in found:
            journal.add_observation(
                fingerprint_id=it.get("fingerprint_id", "?"),
                source=str(it.get("source", "")),
                evidence=str(it.get("evidence", ""))[:400],
                tier=int(it.get("tier", 1)),
            )
            issues.append(it)
        step("scan etendu des journaux", "warn" if found else "ok",
             f"{len(found)} constat(s) sur {len(logs)} fichiers",
             [i.get("fingerprint_id") for i in found])
    except Exception as exc:
        step("scan etendu des journaux", "error", f"{type(exc).__name__}: {exc}")

    # 3 ------------------------------------------------------------------
    cron_report: Dict[str, Any] = {}
    try:
        cron_report = run_cron_health_check(dry_run=True) or {}
        total = cron_report.get("total", "?")
        err = cron_report.get("errors", cron_report.get("error", "?"))
        step("sante des cron jobs", "warn" if cron_report.get("alerts") else "ok",
             f"total={total} erreurs={err}",
             [a.get("name") for a in (cron_report.get("alerts") or [])][:10])
    except Exception as exc:
        step("sante des cron jobs", "error", f"{type(exc).__name__}: {exc}")

    # 4 ------------------------------------------------------------------
    dead: List[str] = []
    scripts_dir = home / "scripts"
    skills_dir = home / "skills"
    for j in jobs:
        sc = j.get("script")
        if sc and not (scripts_dir / sc).exists() and not Path(sc).is_absolute():
            dead.append(f"{j.get('name')}: script manquant {sc}")
        for sk in (j.get("skills") or []):
            if not (skills_dir / str(sk)).is_dir() and not list(skills_dir.rglob(f"{sk}/SKILL.md")):
                dead.append(f"{j.get('name')}: skill manquante {sk}")
    step("conformite des jobs", "warn" if dead else "ok",
         f"{len(dead)} reference(s) morte(s)", dead)

    # 5 ------------------------------------------------------------------
    hygiene: List[str] = []
    for d in ("logs", "cron", "commons/journals", "scripts"):
        p = home / d
        if not p.exists():
            hygiene.append(f"dossier absent: {d}")
    try:
        for p in list((home / "logs").glob("*.jsonl")) + list((home / "logs").glob("*.log")):
            sz = p.stat().st_size
            if sz > 20 * 1024 * 1024:
                hygiene.append(f"{p.name} volumineux ({sz // 1048576} Mo)")
    except OSError:
        pass
    step("hygiene des donnees", "warn" if hygiene else "ok",
         f"{len(hygiene)} point(s)", hygiene)

    # 6 ------------------------------------------------------------------
    gw = _gateway_alive()
    state_path = home / "gateway_state.json"
    gw_state = None
    try:
        gw_state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    code_ref = home / "hermes-agent" / "agent"
    desync = None
    if gw.get("alive") and gw.get("started") and code_ref.is_dir():
        newest = 0.0
        for p in code_ref.rglob("*.py"):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
        if newest and newest - gw["started"] > 300:
            desync = int(newest - gw["started"])
    step("etat du coeur", "warn" if (not gw["alive"] or desync) else "ok",
         f"gateway_vivant={gw['alive']} pid={gw['pid']}"
         + (f" desync={desync}s (disque plus recent que le process)" if desync else "")
         + (f" state={gw_state.get('gateway')}" if isinstance(gw_state, dict) else ""))

    # 7 ------------------------------------------------------------------
    fn = home / "plugins/hermes-field-notes/skills/hermes-field-notes/scripts/fieldnotes.py"
    if fn.exists():
        try:
            r = subprocess.run([sys.executable, str(fn), "patches", "check"],
                               capture_output=True, text=True, timeout=120)
            tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
            # "3 OK · 0 MISSING" CONTIENT la chaine MISSING : lire le compteur, pas le mot.
            m = _re.search(r"(\d+)\s+MISSING", r.stdout or "")
            miss = int(m.group(1)) if m else 0
            bad = r.returncode != 0 or miss > 0
            step("patchs locaux du coeur", "warn" if bad else "ok",
                 (tail[0][:160] if tail else f"rc={r.returncode}"))
        except Exception as exc:
            step("patchs locaux du coeur", "error", f"{type(exc).__name__}: {exc}")
    else:
        step("patchs locaux du coeur", "skipped", "field-notes non installe")

    # 8 ------------------------------------------------------------------
    try:
        n_skills = len(list(skills_dir.rglob("SKILL.md"))) if skills_dir.is_dir() else 0
        n_plugins = len([d for d in (home / "plugins").iterdir() if d.is_dir()])
        step("integrite des skills", "ok" if n_skills else "warn",
             f"{n_skills} SKILL.md, {n_plugins} plugins installes")
    except Exception as exc:
        step("integrite des skills", "error", f"{type(exc).__name__}: {exc}")

    # 9 ------------------------------------------------------------------
    open_issues: List[Dict[str, Any]] = []
    ipath = storage_dir / "issues.jsonl"
    if ipath.exists():
        for line in ipath.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
                if it.get("status", "open") != "resolved":
                    open_issues.append(it)
            except Exception:
                continue
    step("issues connues non resolues", "warn" if open_issues else "ok",
         f"{len(open_issues)} issue(s) ouverte(s)")

    # 10 -----------------------------------------------------------------
    try:
        cm = ConfidenceModel(storage_dir)
        repairable = {i.get("fingerprint_id"): cm.get_score(i.get("fingerprint_id", ""))
                      for i in issues if i.get("fingerprint_id")}
        step("modele de confiance", "ok",
             f"{len(repairable)} empreinte(s) notee(s)",
             [f"{k}={v:.2f}" for k, v in sorted(repairable.items())])
    except Exception as exc:
        step("modele de confiance", "skipped", f"{type(exc).__name__}: {exc}")

    # 11 -----------------------------------------------------------------
    applied, failed, skipped_fix = [], [], []
    try:
        engine = FixEngine(storage_dir, dry_run=not apply_fixes)
        tier1 = [i for i in issues if int(i.get("tier", 1)) == 1]
        if not tier1:
            step("reparation Tier 1", "ok", "aucun constat Tier 1 a reparer")
        else:
            applied, failed = engine.apply_all(tier1)
            # FixResult est un dict : {fix_id, fingerprint, command, description, success, dry_run}
            for f in applied:
                journal.add_action(str(f.get("fingerprint", "")), str(f.get("fix_id", "")),
                                   str(f.get("command", "")),
                                   "applied" if not f.get("dry_run") else "dry-run")
            for f in failed:
                journal.add_action(str(f.get("fingerprint", "")), str(f.get("fix_id", "")),
                                   str(f.get("command", "")), "failed")
            done = {f.get("fingerprint") for f in list(applied) + list(failed)}
            skipped_fix = [i.get("fingerprint_id") for i in tier1
                           if i.get("fingerprint_id") not in done]
            mode = "APPLIQUE" if apply_fixes else "dry-run"
            step("reparation Tier 1", "warn" if failed else "ok",
                 f"{mode} : {len(applied)} ok, {len(failed)} echec, {len(skipped_fix)} sans correcteur",
                 [f"{f.get('fingerprint')}: {f.get('description', '')}"[:80] for f in applied])
    except Exception as exc:
        step("reparation Tier 1", "error", f"{type(exc).__name__}: {exc}")

    # 12 -----------------------------------------------------------------
    escalations = [i for i in issues if int(i.get("tier", 1)) >= 2]
    for i in escalations:
        try:
            journal.add_escalation(str(i.get("fingerprint_id", "?")),
                                   str(i.get("fingerprint_id", "?")),
                                   str(i.get("evidence", ""))[:200],
                                   f"tier-{int(i.get('tier', 2))}")
        except Exception:
            pass
    step("escalades", "warn" if escalations else "ok",
         f"{len(escalations)} constat(s) Tier 2+ (non reparables automatiquement)",
         [i.get("fingerprint_id") for i in escalations][:10])

    # 13 -----------------------------------------------------------------
    try:
        jpath = journal.write()
        entries = journal.get_entries()
    except Exception as exc:
        jpath, entries = None, []
        step("journal et rapport", "error", f"{type(exc).__name__}: {exc}")
    else:
        step("journal et rapport", "ok", f"{len(entries)} entree(s) -> {jpath}")

    statuses = [s["status"] for s in steps]
    return {
        "mode": "deep",
        "steps_declared": len(STEPS),
        "steps_executed": len(steps),
        "status": "error" if "error" in statuses else ("warn" if "warn" in statuses else "ok"),
        "apply_fixes": apply_fixes,
        "findings_total": len(issues) + len(open_issues),
        "duration_s": round(time.time() - t0, 2),
        "steps": steps,
        "journal": str(jpath) if jpath else None,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "note": ("dry-run : aucun correctif applique (relancer avec apply=true pour les Tier 1)"
                 if not apply_fixes else "correctifs Tier 1 appliques"),
    }


def format_deep_report(rep: Dict[str, Any]) -> str:
    """Rendu texte, compact et scannable."""
    icon = {"ok": "OK ", "warn": "!! ", "error": "XX ", "skipped": "-- "}
    lines = [f"Deep scan custodian — statut {rep.get('status')} "
             f"({rep.get('steps_executed')}/{rep.get('steps_declared')} etapes, "
             f"{rep.get('duration_s')}s, {rep.get('findings_total')} constat(s))"]
    lines.append("-" * 72)
    for s in rep.get("steps", []):
        lines.append(f"{icon.get(s['status'], '?  ')} {s['step']:2d}. {s['name']}: {s['detail']}")
        for f in (s.get("findings") or [])[:6]:
            lines.append(f"        - {f}")
    lines.append("-" * 72)
    lines.append(rep.get("note", ""))
    if rep.get("journal"):
        lines.append(f"journal: {rep['journal']}")
    return "\n".join(lines)


if __name__ == "__main__":  # controle executable : le module doit tourner seul
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="appliquer les correctifs Tier 1")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run_deep_scan(apply_fixes=a.apply)
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str) if a.json
          else format_deep_report(r))
