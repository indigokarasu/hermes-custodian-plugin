"""Cron job registration for Custodian plugin.

Registers 4 cron jobs:
1. custodian:light  — quick heartbeat scan
2. custodian:deep   — full 13-step sweep (every 6h)
3. custodian:escalation-runner — process escalated issues (weekday mornings)
4. custodian:update — self-update from GitHub (midnight)
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CRON_JOBS: List[Dict[str, Any]] = [
    {
        "name": "custodian:deep",
        "schedule": "0 1,7,13,19 * * *",
        "prompt": "Run custodian deep scan. Read your Custodian plugin skill for the full 13-step procedure. Use terminal() with heredoc for all file operations — never execute_code in cron mode. If no actionable issues found, respond with exactly '[SILENT]'.",
        "no_agent": False,
    },
    {
        "name": "custodian:escalation-runner",
        "schedule": "*/30 9-17 * * 1-5",
        "prompt": "Run Custodian escalation runner. Read your Custodian plugin skill. Process escalated Tier 3+ issues from issues.jsonl. Use terminal() with heredoc for all file mutations — never read_file on JSONL files (corrupts them), never execute_code (blocked in cron). When running as a cron job, if no escalated issues need processing, respond with exactly '[SILENT]'.",
        "no_agent": False,
    },
    {
        "name": "custodian:update",
        "schedule": "0 0 * * *",
        "prompt": "Run Custodian self-update. Read your Custodian plugin skill for the update procedure. Check GitHub for new commits. Use terminal() with heredoc for all git operations. If already up to date, respond with exactly '[SILENT]'.",
        "no_agent": False,
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
                )
                self.mark_registered(name)
                registered.append(name)
                logger.info("Registered cron job: %s (%s)", name, job["schedule"])
            except Exception as e:
                logger.error("Failed to register cron job %s: %s", name, e)
        return registered

    def get_registered(self) -> List[str]:
        return list(self._registered)
