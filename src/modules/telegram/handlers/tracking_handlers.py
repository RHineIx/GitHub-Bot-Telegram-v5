# src/modules/telegram/handlers/tracking_handlers.py

import logging
import asyncio
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest

from src.core.database import DatabaseManager
from src.modules.github.api import GitHubAPI, GitHubAPIError
from src.modules.telegram.keyboards import TrackingCallback

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(TrackingCallback.filter(F.action == "set_list"))
async def handle_set_tracking_list(
    call: types.CallbackQuery,
    callback_data: TrackingCallback,
    db_manager: DatabaseManager,
    github_api: GitHubAPI,
):
    """Handles selection of a GitHub list to start tracking for releases."""
    await call.message.edit_text("⏳ Processing selection...")
    list_slug = callback_data.value

    # We need the owner's login to build the URL for scraping
    owner_login = await github_api.get_viewer_login()
    if not owner_login:
        await call.message.edit_text("❌ Could not verify GitHub account. Please check your token.")
        return

    await db_manager.clear_release_states()
    await db_manager.set_tracked_list(list_slug)
    await call.message.edit_text(f"⏳ Establishing baseline for list '{list_slug}'... This may take a moment.")

    # Use the new web scraping method
    repo_full_names = await github_api.get_repos_in_list_by_scraping(owner_login, list_slug)
    repo_count = len(repo_full_names) if repo_full_names else 0
    baselined_count = 0
    
    if repo_full_names:
        # Use the new efficient method
        latest_releases = await github_api.get_latest_releases_for_multiple_repos(repo_full_names)

        if latest_releases is not None:
            # Concurrently update the database for all repositories that have releases
            update_tasks = [
                db_manager.update_repository_release_id(repo_name, release_id)
                for repo_name, release_id in latest_releases.items()
            ]
            await asyncio.gather(*update_tasks)
            baselined_count = len(latest_releases)
        else:
            await call.message.edit_text("❌ Failed to fetch release data from GitHub API during baselining.")
            return

    await call.message.edit_text(
        f"✅ **Tracking Enabled**\n\n"
        f"Now monitoring the **{list_slug}** list ({repo_count} repos found).\n"
        f"Established baseline for {baselined_count} repositories with existing releases.",
        parse_mode="Markdown"
    )

@router.callback_query(TrackingCallback.filter(F.action == "stop"))
async def handle_stop_tracking(
    call: types.CallbackQuery,
    db_manager: DatabaseManager,
):
    """Handles the 'Stop Tracking' button press."""
    await call.message.edit_text("⏳ Disabling release tracking...")

    # Clear both the tracked list setting and all stored release states
    await db_manager.clear_tracked_list()
    await db_manager.clear_release_states()
    
    await call.message.edit_text(
        "✅ **Tracking Stopped**\n\n"
        "The bot will no longer monitor for new releases. "
        "You can enable it again at any time using the `/track` command.",
        parse_mode="Markdown"
    )
    await call.answer("Release tracking has been disabled.")