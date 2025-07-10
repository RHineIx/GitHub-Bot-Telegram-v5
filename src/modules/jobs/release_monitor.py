# src/modules/jobs/release_monitor.py

import asyncio
import logging
from typing import Optional

from src.core.database import DatabaseManager
from src.modules.github.api import GitHubAPI, GitHubAPIError
from src.core.config import Settings

logger = logging.getLogger(__name__)

class ReleaseMonitor:
    """A background job that checks for new releases in a tracked list."""

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
            logger.info("Release monitoring task started.")

    def stop(self):
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            logger.info("Release monitoring task has been cancelled.")

    def signal_settings_changed(self):
        self._settings_changed.set()

    async def _run_check_loop(self):
        logger.info("Release check loop is now running.")
        while True:
            try:
                if await self.db_manager.get_tracked_list():
                    await self._check_for_new_releases()

                interval = await self.db_manager.get_release_monitor_interval() or self.settings.default_release_monitor_interval
                
                # Wait for the interval OR a signal that settings have changed
                await asyncio.wait_for(self._settings_changed.wait(), timeout=interval)
                logger.info("Release monitor settings change received, loop will restart.")
                self._settings_changed.clear()

            except asyncio.TimeoutError:
                continue # This is expected, it means the interval finished
            except asyncio.CancelledError:
                logger.info("Release check loop has been cancelled.")
                break
            except Exception as e:
                logger.error(f"An unexpected error in release check loop: {e}", exc_info=True)
                await asyncio.sleep(120)

    async def _check_for_new_releases(self):
        """
        Fetches all repository releases in the tracked list in a single API call
        and compares them against the stored state.
        """
        tracked_list_slug = await self.db_manager.get_tracked_list()
        if not tracked_list_slug:
            return

        owner_login = await self.github_api.get_viewer_login()
        if not owner_login:
            logger.warning("Cannot check for releases, GitHub login not found.")
            return
            
        logger.info(f"Checking for new releases in list: {tracked_list_slug}")
        repo_names = await self.github_api.get_repos_in_list_by_scraping(owner_login, tracked_list_slug)

        if not repo_names:
            logger.warning(f"No repositories found while scraping list '{tracked_list_slug}'.")
            return

        try:
            # --- REFACTORED CORE LOGIC ---
            # 1. Fetch all latest release IDs in one call
            latest_releases_from_api = await self.github_api.get_latest_releases_for_multiple_repos(repo_names)
            if latest_releases_from_api is None:
                logger.error("Failed to fetch multi-repo release data, skipping this check cycle.")
                return

            # 2. Process the results
            for repo_name in repo_names:
                new_release_id = latest_releases_from_api.get(repo_name)
                
                # If the repo has no releases on GitHub, there's nothing to do.
                if not new_release_id:
                    continue

                known_id = await self.db_manager.get_repository_release_id(repo_name)

                # New repo with a release, establish baseline
                if not known_id:
                    logger.info(f"Establishing baseline for new repo {repo_name} with release ID {new_release_id}.")
                    await self.db_manager.update_repository_release_id(repo_name, new_release_id)
                # Release ID has changed, it's a new release
                elif known_id != new_release_id:
                    logger.info(f"New release found for {repo_name}! Old: {known_id}, New: {new_release_id}. Queueing.")
                    await self.repo_queue.put(("release", repo_name))
                    await self.db_manager.update_repository_release_id(repo_name, new_release_id)

            # --- END REFACTOR ---
        except GitHubAPIError as e:
            logger.error(f"A GitHub API error occurred during release check for list {tracked_list_slug}: {e}")
        except Exception as e:
            logger.error(f"A critical error occurred during release check for list {tracked_list_slug}: {e}", exc_info=True)