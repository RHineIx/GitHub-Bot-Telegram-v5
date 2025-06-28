# src/core/logging_setup.py

import asyncio
import html
import logging
from typing import Optional

from aiogram import Bot

from src.core.config import Settings

# This queue will act as a bridge between the sync logging and async sending
log_queue = asyncio.Queue()


class AsyncTelegramLogHandler(logging.Handler):
    """
    A non-blocking logging handler that puts records into an asyncio.Queue.
    """

    def __init__(self):
        super().__init__()

    def emit(self, record: logging.LogRecord):
        """
        Formats the log record and puts the message into the async queue.
        This method is synchronous and non-blocking.
        """
        log_entry = self.format(record)
        log_queue.put_nowait(log_entry)


async def log_sender_task(bot: Bot, channel_id: str):
    """
    An asynchronous task that consumes logs from the queue and sends them.
    """
    while True:
        try:
            log_entry = await log_queue.get()

            # --- FIX: Smarter truncation logic ---
            header = "<b>⭕ ERROR ⭕</b>\n\n"
            # Max length is 4096. Reserve space for header and <pre>...</pre> tags.
            max_content_len = 4096 - len(header) - 7

            safe_log_entry = html.escape(log_entry)

            if len(safe_log_entry) > max_content_len:
                # Truncate the content *inside* the tags
                safe_log_entry = safe_log_entry[: max_content_len - 4] + "..."

            message = f"{header}<pre>{safe_log_entry}</pre>"

            await bot.send_message(channel_id, message)
            log_queue.task_done()
        except Exception as e:
            logging.getLogger(__name__).error(
                f"FATAL: Could not send log to Telegram: {e}"
            )


def setup_logging(settings: Settings) -> bool:
    """
    Configures basic logging and sets up the async Telegram handler if configured.
    Returns True if the Telegram handler was set up, otherwise False.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
    )
    # Reduce noise from libraries
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    if settings.log_channel_id:
        # Configure our custom handler
        telegram_handler = AsyncTelegramLogHandler()
        telegram_handler.setLevel(logging.WARNING)  # Only send WARNING and above
        formatter = logging.Formatter("%(name)s:%(lineno)d - %(message)s")
        telegram_handler.setFormatter(formatter)

        # Add the handler to the root logger to capture everything
        root_logger = logging.getLogger("")
        root_logger.addHandler(telegram_handler)

        logging.info(
            f"TelegramLogHandler configured for channel {settings.log_channel_id}."
        )
        return True

    return False
