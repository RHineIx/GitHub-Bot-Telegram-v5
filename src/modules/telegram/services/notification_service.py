# src/modules/telegram/services/notification_service.py

import logging
from typing import List, Optional, Dict, Any
import asyncio

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputMediaPhoto, InputMediaVideo, InlineKeyboardMarkup, BufferedInputFile

from src.core.database import DatabaseManager
from src.modules.ai.summarizer import AISummarizer
from src.modules.github.api import GitHubAPI
from src.modules.github.formatter import RepoFormatter
from src.modules.github.models import Repository
from src.modules.telegram.keyboards import get_view_on_github_keyboard
from src.utils import (
    extract_media_from_readme,
    get_media_info,
    scrape_social_preview_image,
    is_url_excluded,
    download_image_to_bytes,
)

logger = logging.getLogger(__name__)

# A set of error substrings that indicate a permanent issue with a destination chat.
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
    """
    Asynchronous worker that consumes repository names from a queue and processes them.
    This version includes a 5-second delay BEFORE processing the next item to space out
    resource-intensive AI tasks.
    """
    is_first_item_in_batch = True
    while not stop_event.is_set():
        repo_full_name = None  # Ensure variable is defined for the finally block
        try:
            # Wait for an item from the queue
            notification_type, repo_full_name = await asyncio.wait_for(
                queue.get(), timeout=1.0
            )

            # If this is not the first item in a new batch of tasks, wait for 5 seconds.
            if not is_first_item_in_batch:
                logger.info(f"Waiting 5 seconds before starting AI processing for {repo_full_name}...")
                await asyncio.sleep(5)
            
            # Now that the potential delay is over, process the item.
            await service.process_and_send(notification_type, repo_full_name)
            
            is_first_item_in_batch = False

        except asyncio.TimeoutError:
            # The queue is empty. Reset the flag so the next item that arrives is processed immediately.
            is_first_item_in_batch = True
            continue
        except Exception as e:
            logger.error(f"Error processing {repo_full_name}: {e}", exc_info=True)
        finally:
            # Mark the task as done, whether it succeeded or failed.
            if repo_full_name:
                queue.task_done()


class NotificationService:
    """
    Handles the entire process of creating and sending notifications.
    It orchestrates fetching data, formatting messages, and sending them to Telegram.
    """

    def __init__(
        self,
        bot: Bot,
        db_manager: DatabaseManager,
        github_api: GitHubAPI,
        summarizer: Optional[AISummarizer],
    ):
        """Initializes the service with all its dependencies."""
        self.bot = bot
        self.db_manager = db_manager
        self.github_api = github_api
        self.summarizer = summarizer

    async def _get_notification_media(self, repo: Repository, readme_content: Optional[str]) -> List[InputMediaPhoto | InputMediaVideo]:
        """
        Attempts to get the best available media for a notification, following a fallback sequence.
        1. Try AI-selected media.
        2. If none, try the social preview image.
        Returns a list of media objects or an empty list.
        """
        # --- Attempt 1: AI Media Selection ---
        if self.summarizer and readme_content and await self.db_manager.is_ai_media_selection_enabled():
            all_media_urls = extract_media_from_readme(readme_content, repo)
            if all_media_urls:
                selected_urls = await self.summarizer.select_preview_media(readme_content, all_media_urls)
                if selected_urls:
                    media_group = await self._build_media_group(selected_urls)
                    if media_group:
                        logger.info(f"Successfully built media group using AI selection for {repo.full_name}.")
                        return media_group

        # --- Attempt 2: Social Preview Image Fallback ---
        logger.info(f"AI media selection failed or was disabled for {repo.full_name}. Trying social preview fallback.")
        async with aiohttp.ClientSession() as session:
            social_image_url = await scrape_social_preview_image(repo.url, session)
            if social_image_url:
                logger.info(f"Successfully found social preview image for {repo.full_name}.")
                return [InputMediaPhoto(media=social_image_url)]

        logger.info(f"All media acquisition methods failed for {repo.full_name}.")
        return []

    async def _prepare_star_notification_payload(self, repo: Repository) -> Dict[str, Any]:
        """Prepares the content payload for a star notification."""
        owner, repo_name = repo.full_name.split("/")
        readme_content = await self.github_api.get_readme(owner, repo_name)
        ai_summary = None

        if self.summarizer and readme_content and await self.db_manager.is_ai_summary_enabled():
            ai_summary = await self.summarizer.summarize_readme(readme_content)

        # Proactively wait for 2 seconds to avoid hitting the Gemini free tier rate limit
        # when making two consecutive calls (summarize -> select_media).
        if self.summarizer and readme_content and await self.db_manager.is_ai_media_selection_enabled():
            logger.info("Waiting 2 seconds before media selection to respect API rate limits.")
            await asyncio.sleep(2)
        
        # Centralized media acquisition logic
        media_group = await self._get_notification_media(repo, readme_content)
        
        return {
            "destinations": await self.db_manager.get_all_destinations(),
            "caption": RepoFormatter.format_repository_preview(repo, ai_summary),
            "media_group": media_group,
            "reply_markup": None,
        }

    async def _prepare_release_notification_payload(self, repo: Repository) -> Dict[str, Any]:
        """Prepares the content payload for a release notification."""
        media_group, keyboard = [], None
        if repo.latest_release and repo.latest_release.nodes:
            release_url = repo.latest_release.nodes[0].url
            keyboard = get_view_on_github_keyboard(release_url).as_markup()
            async with aiohttp.ClientSession() as session:
                image_url = await scrape_social_preview_image(release_url, session)
                if image_url:
                    media_group.append(InputMediaPhoto(media=image_url))
        
        return {
            "destinations": await self.db_manager.get_all_release_destinations(),
            "caption": RepoFormatter.format_release_notification(repo),
            "media_group": media_group,
            "reply_markup": keyboard,
        }

    async def process_and_send(self, notification_type: str, repo_full_name: str):
        """Main orchestration method for a single notification."""
        logger.info(f"Starting AI processing for '{repo_full_name}'...")
        owner, repo_name = repo_full_name.split("/")

        repo_data = await self.github_api.get_repository_data_for_notification(owner, repo_name)
        if not repo_data or not repo_data.repository:
            logger.error(f"Could not fetch data for {repo_full_name}. Aborting.")
            return

        repo = repo_data.repository
        payload = {}

        if notification_type == "star":
            payload = await self._prepare_star_notification_payload(repo)
        elif notification_type == "release":
            payload = await self._prepare_release_notification_payload(repo)
        else:
            logger.warning(f"Unknown notification type '{notification_type}'. Aborting.")
            return

        for target_id in payload["destinations"]:
            await self._send_notification(
                repo_full_name=repo.full_name,
                target_id=target_id,
                caption=payload["caption"],
                media_group=payload["media_group"],
                reply_markup=payload["reply_markup"],
            )

    async def _build_media_group(
        self, urls: List[str]
    ) -> List[InputMediaPhoto | InputMediaVideo]:
        """
        Validates media URLs, filters them, and builds a list of Telegram media objects.
        """
        media_group = []
        if not urls:
            return media_group

        async with aiohttp.ClientSession() as session:
            tasks_with_context = []
            
            for url in urls:
                if "github.com/" in url and "/assets/" in url:
                    logger.info(f"Trusting GitHub asset URL, skipping HEAD validation: {url}")
                    if any(vid_ext in url for vid_ext in ['.mp4', '.webm', '.mov']):
                         media_group.append(InputMediaVideo(media=url))
                    else:
                         media_group.append(InputMediaPhoto(media=url))
                else:
                    task = get_media_info(url, session)
                    tasks_with_context.append((url, task))

            if not tasks_with_context:
                return media_group

            validation_results = await asyncio.gather(
                *[task for _, task in tasks_with_context], return_exceptions=True
            )
            
            for i, result in enumerate(validation_results):
                original_url = tasks_with_context[i][0]
                
                if isinstance(result, Exception) or not result or not result[0]:
                    logger.warning(f"Validation failed for media URL '{original_url}'. Reason: {result}")
                    continue
                
                content_type, final_url = result
                if is_url_excluded(final_url):
                    logger.info(f"URL '{final_url}' was filtered out by keyword exclusion.")
                    continue

                if "video" in content_type:
                    media_group.append(InputMediaVideo(media=final_url))
                elif "image" in content_type:
                    media_group.append(InputMediaPhoto(media=final_url))
        
        return media_group

    async def _send_notification(
        self,
        repo_full_name: str,
        target_id: str,
        caption: str,
        media_group: List[InputMediaPhoto | InputMediaVideo],
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ):
        """
        Handles the actual sending logic to Telegram, with multiple fallback layers.
        - Tries sending media by URL.
        - If that fails due to content issues, it downloads the image and sends it as bytes (proxy).
        - If media fails entirely, it sends as a text message with a link preview.
        - If all else fails, it sends as a plain text message.
        """
        chat_id, thread_id = (
            (target_id.split("/")[0], int(target_id.split("/")[1]))
            if "/" in target_id
            else (target_id, None)
        )
        repo_link = f"<a href='https://github.com/{repo_full_name}'>{repo_full_name}</a>"

        try:
            # --- Primary Attempt: Send media by URL ---
            if media_group:
                media_group[0].caption = caption
                media_group[0].parse_mode = "HTML"
                
                if len(media_group) == 1 and isinstance(media_group[0], InputMediaPhoto):
                    await self.bot.send_photo(
                        chat_id=chat_id, photo=media_group[0].media,
                        caption=caption, parse_mode="HTML",
                        message_thread_id=thread_id, reply_markup=reply_markup
                    )
                else:
                    await self.bot.send_media_group(
                        chat_id=chat_id, media=media_group, message_thread_id=thread_id
                    )
            # --- No Media: Send text with link preview ---
            else:
                await self.bot.send_message(
                    chat_id, caption, parse_mode="HTML",
                    disable_web_page_preview=False, # Enable link preview by default
                    message_thread_id=thread_id, reply_markup=reply_markup,
                )
        except TelegramAPIError as e:
            error_message = str(e).lower()

            # --- Handle Permanent Errors (e.g., bot blocked) ---
            if any(p_error in error_message for p_error in PERMANENT_TELEGRAM_ERRORS):
                logger.warning(f"Permanent error for destination {target_id} for {repo_link}: {e}. Removing.")
                await self.db_manager.remove_destination(target_id)
                await self.db_manager.remove_release_destination(target_id)
                return

            # --- Handle Media Content Errors (this is where the proxy logic kicks in) ---
            media_content_errors = ["wrong type of the web page content", "failed to get http url content", "webpage_curl_failed", "webpage_media_empty"]
            if any(media_error in error_message for media_error in media_content_errors):
                logger.warning(f"Could not send media for {repo_link} by URL: {e}. Attempting proxy download.")

                # --- Fallback 1: Download and send image as bytes ---
                if media_group and isinstance(media_group[0], InputMediaPhoto):
                    image_url = media_group[0].media
                    async with aiohttp.ClientSession() as session:
                        image_bytes = await download_image_to_bytes(image_url, session)
                    
                    if image_bytes:
                        try:
                            # Use BufferedInputFile to send bytes
                            photo_file = BufferedInputFile(image_bytes, filename="preview.jpg")
                            await self.bot.send_photo(
                                chat_id=chat_id, photo=photo_file, caption=caption,
                                parse_mode="HTML", message_thread_id=thread_id, reply_markup=reply_markup
                            )
                            logger.info(f"Successfully sent media for {repo_link} via proxy.")
                            return # Success, exit the function
                        except TelegramAPIError as proxy_e:
                            logger.error(f"Proxy fallback also failed for {repo_link}: {proxy_e}")
                
                # --- Fallback 2: Send as text with link preview ---
                logger.warning(f"Proxy download failed for {repo_link}. Retrying as text with link preview.")
                try:
                    await self.bot.send_message(
                        chat_id, caption, parse_mode="HTML", disable_web_page_preview=False,
                        message_thread_id=thread_id, reply_markup=reply_markup,
                    )
                except Exception as final_fallback_e:
                    logger.error(f"Final text-only notification also failed for {repo_link}: {final_fallback_e}")
            else:
                # Handle other, unexpected Telegram API errors
                logger.error(f"Unexpected Telegram API error for repo {repo_link} to target '{target_id}': {e}")