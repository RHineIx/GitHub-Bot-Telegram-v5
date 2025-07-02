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
        Fetches the tracked list, scrapes the repositories, and checks for new
        releases concurrently.
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

        # Create a list of tasks to check each repository concurrently
        check_tasks = [
            self._process_single_repo_release(repo_name) for repo_name in repo_names
        ]
        # Execute all checks in parallel for better performance
        await asyncio.gather(*check_tasks)

    async def _process_single_repo_release(self, repo_name: str):
        """
        Helper method to check and update a single repository's release state.
        This is run concurrently for all repos in the tracked list.
        """
        try:
            repo_data = await self.github_api.get_repository_data_for_notification(*repo_name.split('/'))
            if not (repo_data and repo_data.repository and repo_data.repository.latest_release and repo_data.repository.latest_release.nodes):
                return # No release information found for this repo.

            latest_tag = repo_data.repository.latest_release.nodes[0].tag_name
            known_tag = await self.db_manager.get_repository_release_state(repo_name)

            # If the tag is new, queue a notification.
            if known_tag != latest_tag:
                logger.info(f"New release found for {repo_name}! Old: {known_tag}, New: {latest_tag}. Queueing notification.")
                # Queue the notification with the "release" type
                await self.repo_queue.put(("release", repo_name))
            
            # If this is the first time seeing the repo, establish a baseline.
            if not known_tag:
                logger.info(f"Establishing baseline for {repo_name} with release {latest_tag}.")

            # Update the database with the latest known release tag.
            await self.db_manager.update_repository_release_state(repo_name, latest_tag)

        except GitHubAPIError as e:
            logger.error(f"A GitHub API error occurred during release check for {repo_name}: {e}")
        except Exception as e:
            logger.error(f"A critical error occurred during release check for {repo_name}: {e}", exc_info=True)