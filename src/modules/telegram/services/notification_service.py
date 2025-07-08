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

# A set of error substrings that indicate a permanent issue with a destination chat.
# If these are found in a Telegram API error, the destination will be removed.
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
    This decouples the detection of an event (e.g., a new star) from the processing
    and sending of the notification, making the application more responsive.
    """
    while not stop_event.is_set():
        try:
            # Wait for a new item in the queue, with a timeout to allow graceful shutdown.
            notification_type, repo_full_name = await asyncio.wait_for(
                queue.get(), timeout=1.0
            )
            # Process the item and send the notification.
            await service.process_and_send(notification_type, repo_full_name)
            queue.task_done()
        except asyncio.TimeoutError:
            # This is expected when the queue is empty; just continue the loop.
            continue
        except Exception as e:
            # Log any unexpected errors in the worker itself.
            logger.error(f"Error in notification worker: {e}", exc_info=True)


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

    async def process_and_send(self, notification_type: str, repo_full_name: str):
        """
        Main orchestration method for a single notification.
        
        Args:
            notification_type: The type of notification ('star' or 'release').
            repo_full_name: The full name of the repository (e.g., 'user/repo').
        """
        owner, repo_name = repo_full_name.split("/")
        logger.info(
            f"Processing '{notification_type}' notification for {repo_full_name}..."
        )

        # 1. Fetch all necessary repository data in a single API call.
        repo_data = await self.github_api.get_repository_data_for_notification(
            owner, repo_name
        )
        if not repo_data or not repo_data.repository:
            logger.error(f"Could not fetch data for {repo_full_name}. Aborting.")
            return

        repo = repo_data.repository
        
        # Initialize variables for the notification components.
        destinations: List[str] = []
        media_group: List[InputMediaPhoto | InputMediaVideo] = []
        caption = ""
        keyboard: Optional[InlineKeyboardMarkup] = None

        # 2. Build the notification content based on the type.
        if notification_type == "release":
            destinations = await self.db_manager.get_all_release_destinations()
            caption = RepoFormatter.format_release_notification(repo)
            # For releases, try to get a social preview image from the release page.
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

            # Use AI features if they are enabled and a README exists.
            if (
                self.summarizer
                and await self.db_manager.are_ai_features_enabled()
                and readme_content
            ):
                # Generate an AI summary of the README.
                ai_summary = await self.summarizer.summarize_readme(readme_content)
                # Find and select the best preview media using AI.
                all_media = extract_media_from_readme(readme_content, repo)
                if all_media:
                    selected_urls = await self.summarizer.select_preview_media(
                        readme_content, all_media
                    )
                    media_group = await self._build_media_group(selected_urls)

            # If no media was found via AI, fall back to the repo's social preview image.
            if not media_group:
                main_repo_url = f"https://github.com/{owner}/{repo_name}"
                async with aiohttp.ClientSession() as session:
                    social_image_url = await scrape_social_preview_image(
                        main_repo_url, session
                    )
                    if social_image_url:
                        media_group.append(InputMediaPhoto(media=social_image_url))
            
            # Format the final caption for the star notification.
            caption = RepoFormatter.format_repository_preview(repo, ai_summary)

        if not destinations:
            logger.warning(f"No destinations found for type '{notification_type}' on repo {repo_full_name}. Aborting.")
            return

        # 3. Send the composed notification to all configured destinations.
        for target_id in destinations:
            await self._send_notification(repo_full_name, target_id, caption, media_group, reply_markup=keyboard)

    async def _build_media_group(
        self, urls: List[str]
    ) -> List[InputMediaPhoto | InputMediaVideo]:
        """Validates media URLs and builds a list of Telegram media objects."""
        media_group = []
        if not urls:
            return media_group
        
        async with aiohttp.ClientSession() as session:
            # Validate all URLs concurrently for performance.
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
        repo_full_name: str,
        target_id: str,
        caption: str,
        media_group: List[InputMediaPhoto | InputMediaVideo],
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ):
        """Handles the actual sending logic to Telegram, including error handling."""
        # Parse the target_id which might contain a thread_id (e.g., '-100.../123')
        chat_id, thread_id = (
            (target_id.split("/")[0], int(target_id.split("/")[1]))
            if "/" in target_id
            else (target_id, None)
        )
        try:
            if media_group:
                media_group[0].caption = caption
                media_group[0].parse_mode = "HTML"
                
                # Use send_photo for single images for better display options (like buttons).
                if len(media_group) == 1 and isinstance(media_group[0], InputMediaPhoto):
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=media_group[0].media,
                        caption=caption,
                        parse_mode="HTML",
                        message_thread_id=thread_id,
                        reply_markup=reply_markup,
                    )
                else:
                    # Send as a media group for multiple items or videos.
                    await self.bot.send_media_group(
                        chat_id=chat_id, 
                        media=media_group, 
                        message_thread_id=thread_id
                    )
            else:
                # Send as a plain text message if no media is available.
                await self.bot.send_message(
                    chat_id,
                    caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True if reply_markup else False,
                    message_thread_id=thread_id,
                    reply_markup=reply_markup,
                )
        except TelegramAPIError as e:
            # Handle errors from the Telegram API.
            error_message = str(e).lower()
            repo_link = f"<a href='https://github.com/{repo_full_name}'>{repo_full_name}</a>"

            if (
                "wrong type of the web page content" in error_message or
                "failed to get http url content" in error_message or
                "webpage_curl_failed" in error_message or
                "webpage_media_empty" in error_message
            ):
                logger.warning(
                    f"Could not send media for {repo_link} due to URL error: {e}. Retrying as text-only."
                )
                try:
                    # Retry sending the notification as a text-only message.
                    # We disable web page preview here to prevent the same error from happening again on a link in the text.
                    await self.bot.send_message(
                        chat_id,
                        caption,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        message_thread_id=thread_id,
                        reply_markup=reply_markup,
                    )
                except Exception as fallback_e:
                    logger.error(f"Fallback text-only notification also failed for {repo_link}: {fallback_e}")
                return # Stop further processing for this error
            
            # If the error is permanent (e.g., bot kicked), remove the destination.
            if any(p_error in error_message for p_error in PERMANENT_TELEGRAM_ERRORS):
                logger.warning(
                    f"Permanent error for destination {target_id} while sending for {repo_link}: {e}. Removing."
                )
                await self.db_manager.remove_destination(target_id)
                await self.db_manager.remove_release_destination(target_id)
            else:
                # For temporary errors, just log it with the helpful context.
                logger.error(f"Telegram API error for repo {repo_link} to target '{target_id}': {e}")
