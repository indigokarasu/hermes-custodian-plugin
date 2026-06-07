# Hermes Custodian Plugin

Operational monitoring, auto-repair, and escalation for Hermes. Monitors gateway logs, cron jobs, skill journals, and OCAS data directories for failures, applies safe non-destructive fixes autonomously, and escalates root-cause analysis for what it cannot fix.

## Installation

```bash
hermes plugins install indigokarasu/hermes-custodian-plugin
```

**The plugin installs disabled by default.** Enable only after review:

```bash
hermes plugins enable custodian
```

## How It Works

- **4 lifecycle hooks**: `post_tool_call`, `on_session_start`, `on_session_end`, `on_session_reset`
- **3 registered tools**: `custodian_status`, `custodian_scan`, `custodian_issues`
- **14 slash commands**: `/custodian status`, `/custodian scan light`, `/custodian scan deep`, `/custodian issues list`, `/custodian issues resolve <id>`, `/custodian repair auto`, `/custodian repair plan`, `/custodian verify <fix_id>`, `/custodian schedule show`, `/custodian confidence show`, `/custodian init`, `/custodian update`, `/custodian escalation-runner`
- **21 known issue fingerprints** matched against gateway logs, cron journals, and OCAS data
- **Confidence model** with auto tier promote/demote based on fix effectiveness
- **15 Tier 1 auto-fixes** applied during quiet hours
- **4 registered cron jobs**: light scan, deep scan, escalation runner, self-update
- **Dashboard panel** with status toggle, issue list, confidence scores, and quick actions

## Configuration

The plugin is opt-in. Add to `config.yaml`:

```yaml
plugins:
  enabled:
    - custodian
```

## Storage

All Custodian data resolves via `HERMES_HOME`:

```
{HERMES_HOME}/commons/data/ocas-custodian/   # issue/fix/effectiveness records
{HERMES_HOME}/commons/journals/ocas-custodian/  # scan journals
```
