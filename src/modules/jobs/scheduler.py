# src/modules/jobs/scheduler.py

import asyncio
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.database import DatabaseManager

logger = logging.getLogger(__name__)


class DigestScheduler:
    """Schedules and executes daily and weekly digest jobs."""

    def __init__(self, db_manager: DatabaseManager, repo_queue: asyncio.Queue):
        self.db_manager = db_manager
        self.repo_queue = repo_queue
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self):
        self.scheduler.add_job(self.send_daily_digest, "cron", hour=21, minute=0)
        self.scheduler.add_job(
            self.send_weekly_digest, "cron", day_of_week="sun", hour=21, minute=0
        )
        self.scheduler.start()
        logger.info(
            "Digest scheduler started. Daily job at 21:00 UTC, Weekly on Sunday at 21:00 UTC."
        )

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Digest scheduler has been shut down.")

    def get_next_run_time(self) -> Optional[datetime]:
        if self.scheduler.running and (jobs := self.scheduler.get_jobs()):
            return min(
                (job.next_run_time for job in jobs if job.next_run_time), default=None
            )
        return None

    async def send_daily_digest(self):
        if await self.db_manager.get_digest_mode() == "daily":
            logger.info("Running daily digest job...")
            await self._send_digest()

    async def send_weekly_digest(self):
        if await self.db_manager.get_digest_mode() == "weekly":
            logger.info("Running weekly digest job...")
            await self._send_digest()

    async def _send_digest(self):
        queued_repos = await self.db_manager.get_and_clear_digest_queue()
        if not queued_repos:
            logger.info("Digest job ran, but the queue was empty.")
            return

        logger.info(
            f"Found {len(queued_repos)} items in digest queue. Adding to processing queue."
        )
        for repo_full_name in queued_repos:
            await self.repo_queue.put(repo_full_name)
