# src/bot.py

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.core.config import settings
from src.core.database import DatabaseManager
from src.core.logging_setup import log_sender_task, setup_logging
from src.modules.ai.summarizer import AISummarizer
from src.modules.github.api import GitHubAPI
from src.modules.jobs.monitor import RepositoryMonitor
from src.modules.jobs.release_monitor import ReleaseMonitor
from src.modules.jobs.scheduler import DigestScheduler
from src.modules.telegram.handlers import (
    command_handlers,
    settings_handlers,
    tracking_handlers,
)
from src.modules.telegram.services.notification_service import (
    NotificationService,
)

logger = logging.getLogger(__name__)


async def notification_worker(
    queue: asyncio.Queue, service: NotificationService, stop_event: asyncio.Event
):
    while not stop_event.is_set():
        try:
            repo_full_name = await asyncio.wait_for(queue.get(), timeout=1.0)
            await service.process_and_send(repo_full_name)
            queue.task_done()
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.error(f"Error in notification worker: {e}", exc_info=True)


async def run():
    log_handler_enabled = setup_logging(settings)
    start_time = datetime.now(timezone.utc)
    logger.info("Starting GraphQL Bot...")

    db_manager = DatabaseManager()
    await db_manager.init_db()

    # The GitHubAPI is now our new GraphQL version
    github_api = GitHubAPI(db_manager=db_manager, settings=settings)
    summarizer = AISummarizer(settings) if settings.gemini_api_key else None

    repo_queue = asyncio.Queue()
    stop_event = asyncio.Event()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(
        db_manager=db_manager,
        github_api=github_api,
        summarizer=summarizer,
        settings=settings,
        start_time=start_time,
    )

    notification_service = NotificationService(bot, db_manager, github_api, summarizer)
    scheduler = DigestScheduler(db_manager, repo_queue)

    star_monitor = RepositoryMonitor(db_manager, github_api, settings, repo_queue)
    release_monitor = ReleaseMonitor(db_manager, github_api, repo_queue)


    dp["monitor"] = star_monitor
    dp["scheduler"] = scheduler

    dp.include_router(command_handlers.router)
    dp.include_router(settings_handlers.router)
    dp.include_router(tracking_handlers.router)

    background_tasks = set()
    if log_handler_enabled:
        background_tasks.add(
            asyncio.create_task(log_sender_task(bot, settings.log_channel_id))
        )

    star_monitor.start()
    release_monitor.start()
    scheduler.start()
    background_tasks.add(
        asyncio.create_task(
            notification_worker(repo_queue, notification_service, stop_event)
        )
    )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Bot is shutting down...")
        stop_event.set()
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        star_monitor.stop()
        release_monitor.stop()
        scheduler.stop()
        await github_api.close()
        await db_manager.close()
        await bot.session.close()
        logger.info("Bot has stopped.")
