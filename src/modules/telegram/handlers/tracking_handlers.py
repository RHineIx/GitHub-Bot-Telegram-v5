# src/modules/telegram/handlers/tracking_handlers.py

import logging
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest

from src.core.database import DatabaseManager
from src.modules.github.api import GitHubAPI
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
    
    baselined_count = 0
    if repo_full_names:
        for repo_full_name in repo_full_names:
            full_repo_data = await github_api.get_repository_data_for_notification(
                *repo_full_name.split("/")
            )
            if (
                full_repo_data
                and full_repo_data.repository
                and full_repo_data.repository.latest_release
                and full_repo_data.repository.latest_release.nodes
            ):
                latest_release_id = full_repo_data.repository.latest_release.nodes[0].id
                await db_manager.update_repository_release_id(repo_full_name, latest_release_id)
                baselined_count += 1
    
    repo_count = len(repo_full_names) if repo_full_names else 0
    await call.message.edit_text(
        f"✅ **Tracking Enabled**\n\n"
        f"Now monitoring the **{list_slug}** list ({repo_count} repos found via scraping).\n"
        f"Established baseline for {baselined_count} repositories with existing releases.",
        parse_mode="Markdown"
    )
