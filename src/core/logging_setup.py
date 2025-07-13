# src/core/logging_setup.py

import asyncio
import html
import logging
import sys
from typing import Optional

from aiogram import Bot
from loguru import logger

from src.core.config import Settings

# This queue will bridge the loguru sink and the async sender task.
log_queue = asyncio.Queue()


class InterceptHandler(logging.Handler):
    """
    A custom logging handler that intercepts standard logging records
    and redirects them to the Loguru logger.
    """
    def emit(self, record: logging.LogRecord):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


class TelegramSink:
    """A Loguru sink that puts formatted log messages into an asyncio Queue."""
    def write(self, message):
        log_queue.put_nowait(message.strip())


async def log_sender_task(bot: Bot, channel_id: str):
    """
    An asynchronous task that consumes logs from the queue and sends them to Telegram.
    """
    while True:
        try:
            log_entry = await log_queue.get()
            header = "<b>⭕ LOG WARNING/ERROR ⭕</b>\n\n"
            max_content_len = 4096 - len(header) - 7
            safe_log_entry = html.escape(log_entry)

            if len(safe_log_entry) > max_content_len:
                safe_log_entry = safe_log_entry[: max_content_len - 4] + "..."

            message = f"{header}<pre>{safe_log_entry}</pre>"
            await bot.send_message(channel_id, message)
            log_queue.task_done()
        except asyncio.CancelledError:
            logger.info("Log sender task cancelled.")
            break
        except Exception:
            logger.opt(exception=True).error("FATAL: Could not send log to Telegram.")


def setup_logging(settings: Settings) -> bool:
    """
    Configures Loguru for console and optional Telegram logging.
    This setup intercepts standard logging calls to provide unified output.
    Returns True if the Telegram handler was set up, otherwise False.
    """
    logger.remove()

    # Console sink now uses the log level from settings
    logger.add(
        sys.stderr,
        level=settings.console_log_level.upper(),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )
    
    # Intercept standard logging to redirect to Loguru
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Set levels for noisy libraries
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    # Telegram sink setup
    telegram_handler_enabled = False
    if settings.log_channel_id:
        # Telegram sink now uses the log level from settings
        logger.add(
            TelegramSink(),
            level=settings.telegram_log_level.upper(),
            format="{name}:{line} - {message}",
        )
        logger.info(f"Loguru Telegram sink configured for channel {settings.log_channel_id} at level {settings.telegram_log_level.upper()}.")
        telegram_handler_enabled = True
    
    return telegram_handler_enabled