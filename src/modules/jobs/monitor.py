# src/modules/jobs/monitor.py

import asyncio
import logging
from typing import Optional

from src.core.config import Settings
from src.core.database import DatabaseManager
from src.modules.github.api import GitHubAPI, GitHubAPIError

logger = logging.getLogger(__name__)


class RepositoryMonitor:
    """A background job that periodically checks for new starred repositories."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        github_api: GitHubAPI,
        settings: Settings,
        repo_queue: asyncio.Queue,
    ):
        self.db_manager = db_manager
        self.github_api = github_api
        self.settings = settings
        self.repo_queue = repo_queue
        self._monitor_task: Optional[asyncio.Task] = None
        self._settings_changed = asyncio.Event()

    def start(self):
        if not self._monitor_task or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._run_check_loop())
            logger.info("Repository monitoring task started.")

    def stop(self):
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            logger.info("Repository monitoring task has been cancelled.")

    def signal_settings_changed(self):
        self._settings_changed.set()

    async def _is_safe_to_monitor(self) -> bool:
        if await self.db_manager.is_monitoring_paused():
            logger.info("Monitoring is paused. Skipping check cycle.")
            return False
        if not await self.db_manager.token_exists():
            logger.debug("No GitHub token found. Skipping monitoring cycle.")
            return False
        return True

    async def _run_check_loop(self):
        logger.info("Star check loop is now running.")
        while True:
            try:
                if await self._is_safe_to_monitor():
                    await self._check_for_new_stars()
                interval = (
                    await self.db_manager.get_stars_monitor_interval()
                    or self.settings.default_stars_monitor_interval
                )
                await asyncio.wait_for(self._settings_changed.wait(), timeout=interval)
                logger.info(
                    "Settings change signal received, loop will restart immediately."
                )
                self._settings_changed.clear()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                logger.info("Star check loop has been cancelled.")
                break
            except Exception as e:
                logger.error(
                    f"An unexpected error in star check loop: {e}", exc_info=True
                )
                await asyncio.sleep(120)

    async def _check_for_new_stars(self):
        logger.info("Checking for new starred repositories...")
        try:
            starred_events = (
                await self.github_api.get_authenticated_user_starred_events()
            )
            if not starred_events:
                return

            last_check_timestamp = await self.db_manager.get_last_check_timestamp()
            if not last_check_timestamp:
                await self.db_manager.update_last_check_timestamp(
                    starred_events[0].starred_at.isoformat()
                )
                logger.info("First run for stars. Baseline timestamp established.")
                return

            new_starred_events = [
                event
                for event in starred_events
                if event.starred_at.isoformat() > last_check_timestamp
            ]
            if not new_starred_events:
                logger.info("No new starred repositories found.")
                return

            new_starred_events.reverse()
            logger.info(f"Found {len(new_starred_events)} new starred repositories.")

            digest_mode = await self.db_manager.get_digest_mode()
            for event in new_starred_events:
                if digest_mode == "off":
                    logger.info(
                        f"Queueing {event.repository.full_name} for instant notification."
                    )
                    await self.repo_queue.put(event.repository.full_name)
                else:
                    logger.info(f"Adding {event.repository.full_name} to digest queue.")
                    await self.db_manager.add_repo_to_digest(event.repository.full_name)

            await self.db_manager.update_last_check_timestamp(
                starred_events[0].starred_at.isoformat()
            )
        except GitHubAPIError as e:
            logger.error(f"A GitHub API error occurred during star check: {e}")
        except Exception as e:
            logger.error(
                f"A critical error occurred during star checking: {e}", exc_info=True
            )
