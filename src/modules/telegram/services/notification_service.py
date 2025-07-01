# src/modules/telegram/services/notification_service.py

import logging
from typing import List, Optional
import asyncio

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputMediaPhoto, InputMediaVideo, InlineKeyboardMarkup

from src.core.database import DatabaseManager
from src.modules.ai.summarizer import AISummarizer
from src.modules.github.api import GitHubAPI
from src.modules.github.formatter import RepoFormatter
from src.modules.telegram.keyboards import get_view_on_github_keyboard
from src.utils import (
    extract_media_from_readme,
    get_media_info,
    scrape_social_preview_image,
)

logger = logging.getLogger(__name__)

PERMANENT_TELEGRAM_ERRORS = {
    "chat not found",
    "bot was kicked",
    "bot was blocked by the user",
    "user is deactivated",
    "chat was deleted",
}


async def notification_worker(
    queue: asyncio.Queue, service: "NotificationService", stop_event: asyncio.Event
):
    while not stop_event.is_set():
        try:
            notification_type, repo_full_name = await asyncio.wait_for(
                queue.get(), timeout=1.0
            )
            await service.process_and_send(notification_type, repo_full_name)
            queue.task_done()
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.error(f"Error in notification worker: {e}", exc_info=True)


class NotificationService:
    """Processes and sends notifications."""

    def __init__(
        self,
        bot: Bot,
        db_manager: DatabaseManager,
        github_api: GitHubAPI,
        summarizer: Optional[AISummarizer],
    ):
        self.bot = bot
        self.db_manager = db_manager
        self.github_api = github_api
        self.summarizer = summarizer

    async def process_and_send(self, notification_type: str, repo_full_name: str):
        """Orchestrates fetching data and sending a notification for a repo."""
        owner, repo_name = repo_full_name.split("/")
        logger.info(
            f"Processing '{notification_type}' notification for {repo_full_name}..."
        )

        repo_data = await self.github_api.get_repository_data_for_notification(
            owner, repo_name
        )
        if not repo_data or not repo_data.repository:
            logger.error(f"Could not fetch data for {repo_full_name}. Aborting.")
            return

        repo = repo_data.repository
        destinations: List[str] = []
        media_group: List[InputMediaPhoto | InputMediaVideo] = []
        caption = ""
        keyboard: Optional[InlineKeyboardMarkup] = None

        if notification_type == "release":
            destinations = await self.db_manager.get_all_release_destinations()
            caption = RepoFormatter.format_release_notification(repo)
            if repo.latest_release and repo.latest_release.nodes:
                release_url = repo.latest_release.nodes[0].url
                keyboard = get_view_on_github_keyboard(release_url).as_markup()
                async with aiohttp.ClientSession() as session:
                    image_url = await scrape_social_preview_image(release_url, session)
                    if image_url:
                        media_group.append(InputMediaPhoto(media=image_url))

        elif notification_type == "star":
            destinations = await self.db_manager.get_all_destinations()
            readme_content = await self.github_api.get_readme(owner, repo_name)
            ai_summary = None
            if (
                self.summarizer
                and await self.db_manager.are_ai_features_enabled()
                and readme_content
            ):
                ai_summary = await self.summarizer.summarize_readme(readme_content)
                all_media = extract_media_from_readme(readme_content, repo)
                if all_media:
                    selected_urls = await self.summarizer.select_preview_media(
                        readme_content, all_media
                    )
                    media_group = await self._build_media_group(selected_urls)

            if not media_group:
                main_repo_url = f"https://github.com/{owner}/{repo_name}"
                async with aiohttp.ClientSession() as session:
                    social_image_url = await scrape_social_preview_image(
                        main_repo_url, session
                    )
                    if social_image_url:
                        media_group.append(InputMediaPhoto(media=social_image_url))
            
            caption = RepoFormatter.format_repository_preview(repo, ai_summary)

        if not destinations:
            logger.warning(f"No destinations found for type '{notification_type}'. Aborting.")
            return

        for target_id in destinations:
            await self._send_notification(target_id, caption, media_group, reply_markup=keyboard)

    async def _build_media_group(
        self, urls: List[str]
    ) -> List[InputMediaPhoto | InputMediaVideo]:
        media_group = []
        if not urls:
            return media_group
        async with aiohttp.ClientSession() as session:
            validation_tasks = [get_media_info(url, session) for url in urls]
            media_info_results = await asyncio.gather(
                *validation_tasks, return_exceptions=True
            )
            for i, result in enumerate(media_info_results):
                original_url = urls[i]
                if isinstance(result, Exception) or not result or not result[0]:
                    logger.warning(
                        f"Validation failed for media URL '{original_url}'. Reason: {result}"
                    )
                    continue
                content_type, final_url = result
                if "video" in content_type:
                    media_group.append(InputMediaVideo(media=final_url))
                elif "image" in content_type:
                    media_group.append(InputMediaPhoto(media=final_url))
        return media_group

    async def _send_notification(
        self,
        target_id: str,
        caption: str,
        media_group: List[InputMediaPhoto | InputMediaVideo],
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ):
        chat_id, thread_id = (
            (target_id.split("/")[0], int(target_id.split("/")[1]))
            if "/" in target_id
            else (target_id, None)
        )
        try:
            if media_group:
                media_group[0].caption = caption
                media_group[0].parse_mode = "HTML"
                if len(media_group) == 1 and isinstance(
                    media_group[0], InputMediaPhoto
                ):
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=media_group[0].media,
                        caption=caption,
                        parse_mode="HTML",
                        message_thread_id=thread_id,
                        reply_markup=reply_markup,
                    )
                else:
                    await self.bot.send_media_group(
                        chat_id=chat_id, 
                        media=media_group, 
                        message_thread_id=thread_id
                        # Note: send_media_group doesn't support reply_markup on the whole group
                    )
            else:
                await self.bot.send_message(
                    chat_id,
                    caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True if reply_markup else False,
                    message_thread_id=thread_id,
                    reply_markup=reply_markup,
                )
        except TelegramAPIError as e:
            error_message = str(e).lower()
            if any(p_error in error_message for p_error in PERMANENT_TELEGRAM_ERRORS):
                logger.warning(
                    f"Permanent error for destination {target_id}: {e}. Removing."
                )
                await self.db_manager.remove_destination(target_id)
            else:
                logger.error(f"Temporary Telegram API error for {target_id}: {e}")