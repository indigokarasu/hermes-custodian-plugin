"""Cron job registration for Custodian plugin.

Registers 3 cron jobs:
1. custodian:deep   — full 13-step sweep (every 6h)
2. custodian:cron-health — cron job health check (4x daily)
3. custodian:escalation-runner — process escalated issues (weekday mornings)
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Shared silence clause. The marker must OPEN the response: the cron lane's
# matcher (gateway.response_filters.is_autonomous_silence_response) suppresses only when
# the marker is the whole response, its own first/last line, or the opening sentinel.
# Prose placed before the marker on the same line makes it "buried mid-sentence" →
# delivered. Models reliably emit a summary sentence first unless told not to, which
# is how "[SILENT]"-marked jobs started announcing that they had nothing to report.
_SILENCE_CLAUSE = (
    "\n\nWhen there is nothing to report, your ENTIRE final response must be the single "
    "line below, emitted FIRST with no preamble, summary, or explanation before or after it:\n"
    "[SILENT]\n"
    "Do not describe what you checked or what you found. Write nothing else — no sentence "
    "may precede the marker."
)

# The skill these jobs load. It MUST be the real skill name: the prompts used to say
# "Read your Custodian plugin skill", which resolves to nothing — there is no skill
# named `custodian` (or `custodian-cron`), only `custodian-health-checks`. The jobs
# logged `Skill 'custodian' not found` and ran without their procedure.
_CUSTODIAN_SKILL = "custodian-health-checks"

CRON_JOBS: List[Dict[str, Any]] = [
    {
        "name": "custodian:deep",
        "schedule": "0 1,7,13,19 * * *",
        "prompt": f"Run custodian deep scan. Read the `{_CUSTODIAN_SKILL}` skill for the full 13-step procedure. Use terminal() with heredoc for all file operations — never execute_code in cron mode."
        + _SILENCE_CLAUSE,
        "no_agent": False,
        "skills": [_CUSTODIAN_SKILL],
    },
    {
        "name": "custodian:cron-health",
        "schedule": "0 8,14,20,2 * * *",
        "prompt": f"Run cron health check. Use the custodian_cron_health tool (dry_run=false). If the report shows alerts (failure_streak >= 1, new errors, or error_count > 10), include the daily_health_line and alert details in your response. Refer to the `{_CUSTODIAN_SKILL}` skill."
        + _SILENCE_CLAUSE,
        "no_agent": False,
        "skills": [_CUSTODIAN_SKILL],
    },
    {
        "name": "custodian:escalation-runner",
        "schedule": "*/30 9-17 * * 1-5",
        "prompt": f"Run Custodian escalation runner. Read the `{_CUSTODIAN_SKILL}` skill. Process escalated Tier 3+ issues from issues.jsonl. Use terminal() with heredoc for all file mutations — never read_file on JSONL files (corrupts them), never execute_code (blocked in cron)."
        + _SILENCE_CLAUSE,
        "no_agent": False,
        "skills": [_CUSTODIAN_SKILL],
    },
]


class CronRegistrar:
    """Registration helper for Custodian cron jobs.

    In plugin context, cron jobs are typically registered via the cronjob tool
    during the /custodian init command rather than at plugin load time.
    This class provides the job definitions and registration logic.
    """

    def __init__(self):
        self.jobs = list(CRON_JOBS)
        self._registered: List[str] = []

    def get_job_definitions(self) -> List[Dict[str, Any]]:
        """Return all cron job definitions."""
        return list(self.jobs)

    def get_job_names(self) -> List[str]:
        """Return all job names."""
        return [j["name"] for j in self.jobs]

    def is_registered(self, job_name: str) -> bool:
        """Check if a job name has been registered."""
        return job_name in self._registered

    def mark_registered(self, job_name: str) -> None:
        """Mark a job as registered."""
        if job_name not in self._registered:
            self._registered.append(job_name)

    def register_all(self, cronjob_fn) -> List[str]:
        """Register all jobs using the provided cronjob function.

        The cronjob_fn should accept the same kwargs as the cronjob tool:
        action='create', name=..., schedule=..., prompt=..., no_agent=...

        Returns list of registered job names.
        """
        registered = []
        for job in self.jobs:
            name = job["name"]
            if self.is_registered(name):
                logger.info("Cron job %s already registered, skipping", name)
                continue
            try:
                cronjob_fn(
                    action="create",
                    name=name,
                    schedule=job["schedule"],
                    prompt=job["prompt"],
                    no_agent=job.get("no_agent", False),
                    skills=job.get("skills", []),
                )
                self.mark_registered(name)
                registered.append(name)
                logger.info("Registered cron job: %s (%s)", name, job["schedule"])
            except Exception as e:
                logger.error("Failed to register cron job %s: %s", name, e)
        return registered

    def get_registered(self) -> List[str]:
        return list(self._registered)
