# hermes-custodian-plugin

<p align="center">
  <img src="./assets/readme/hero.jpg" width="100%" alt="Custodian: operational monitoring, auto-repair, and escalation for Hermes Agent">
</p>

Custodian is a Hermes Agent plugin that monitors gateway logs, cron jobs, skill journals, and OCAS data directories. It classifies issues, applies Tier 1 auto-fixes during quiet hours, and escalates when confidence is low.

**Capabilities:**
- Lifecycle hooks: `post_tool_call`, `on_session_start`, `on_session_end`, `on_session_reset`
- Tools: `custodian_status`, `custodian_scan`, `custodian_issues`
- Slash commands: `/custodian status`, `/custodian scan light`, `/custodian scan deep`, `/custodian issues list`, `/custodian repair auto`, `/custodian schedule show`
- Dashboard panel with status, issue list, and confidence scores
