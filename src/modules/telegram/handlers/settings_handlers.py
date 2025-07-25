# src/modules/telegram/handlers/settings_handlers.py

import logging
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest

from src.core.config import Settings
from src.core.database import DatabaseManager
from src.modules.jobs.monitor import RepositoryMonitor
from src.modules.jobs.release_monitor import ReleaseMonitor
from src.modules.telegram.keyboards import (
    get_ai_submenu_keyboard,
    get_intervals_submenu_keyboard,
    get_interval_submenu_keyboard,
    get_release_interval_submenu_keyboard,
    get_settings_menu_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()


async def _edit_to_main_menu(message: types.Message, db_manager: DatabaseManager):
    """Helper function to edit a message to show the main settings menu."""
    try:
        keyboard = await get_settings_menu_keyboard(db_manager)
        await message.edit_text("⚙️ Bot Settings", reply_markup=keyboard.as_markup())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug("Message not modified, ignoring.")
        else:
            raise

async def _edit_to_ai_menu(message: types.Message, db_manager: DatabaseManager):
    """Helper function to edit a message to show the AI settings submenu."""
    try:
        keyboard = await get_ai_submenu_keyboard(db_manager)
        await message.edit_text("🤖 AI Settings", reply_markup=keyboard.as_markup())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug("Message not modified, ignoring.")
        else:
            raise

async def _edit_to_intervals_menu(message: types.Message, db_manager: DatabaseManager, settings: Settings):
    """Helper function to edit a message to show the intervals hub menu."""
    try:
        keyboard = await get_intervals_submenu_keyboard(db_manager, settings)
        await message.edit_text("⏱️ Interval Settings", reply_markup=keyboard.as_markup())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug("Message not modified, ignoring.")
        else:
            raise

@router.callback_query(F.data.startswith("cb:"))
async def handle_settings_callback(
    call: types.CallbackQuery,
    db_manager: DatabaseManager,
    monitor: RepositoryMonitor,
    release_monitor: ReleaseMonitor, # Injected from dispatcher
    settings: Settings,
):
    await call.answer()
    try:
        _, action, value = call.data.split(":", 2)
    except ValueError:
        logger.warning(f"Received malformed callback data: {call.data}")
        return

    if action == "toggle_pause":
        await db_manager.set_monitoring_paused(
            not await db_manager.is_monitoring_paused()
        )
        await _edit_to_main_menu(call.message, db_manager)
    elif action == "main_menu":
        await _edit_to_main_menu(call.message, db_manager)
    elif action == "open_ai_menu":
        await _edit_to_ai_menu(call.message, db_manager)
    elif action == "toggle_ai_summary":
        await db_manager.set_ai_summary_enabled(
            not await db_manager.is_ai_summary_enabled()
        )
        await _edit_to_ai_menu(call.message, db_manager)
    elif action == "toggle_ai_media":
        await db_manager.set_ai_media_selection_enabled(
            not await db_manager.is_ai_media_selection_enabled()
        )
        await _edit_to_ai_menu(call.message, db_manager)
    elif action == "open_intervals_menu":
        await _edit_to_intervals_menu(call.message, db_manager, settings)
    elif action == "open_interval_menu":
        keyboard = await get_interval_submenu_keyboard(db_manager, settings)
        await call.message.edit_text(
            "⚙️ Select Stars Monitoring Interval:", reply_markup=keyboard.as_markup()
        )
    elif action == "open_release_menu":
        keyboard = await get_release_interval_submenu_keyboard(db_manager, settings)
        await call.message.edit_text(
            "🚀 Select Release Monitoring Interval:", reply_markup=keyboard.as_markup()
        )
    elif action == "set_stars_interval":
        new_interval = int(value)
        await db_manager.update_stars_monitor_interval(new_interval)
        monitor.signal_settings_changed()
        await _edit_to_intervals_menu(call.message, db_manager, settings)
    elif action == "set_release_interval":
        new_interval = int(value)
        await db_manager.update_release_monitor_interval(new_interval)
        release_monitor.signal_settings_changed()
        await _edit_to_intervals_menu(call.message, db_manager, settings)
    elif action == "confirm_remove_token":
        await db_manager.remove_token()
        await db_manager.set_monitoring_paused(True)
        await call.message.edit_text(
            "🗑️ **Token Removed.** Monitoring has been paused.",
            parse_mode="Markdown",
            reply_markup=None,
        )
    elif action == "cancel_action":
        await call.message.edit_text("Action cancelled.", reply_markup=None)
    elif action == "close":
        try:
            await call.message.delete()
        except TelegramBadRequest:
            # Message might have already been deleted, ignore.
            pass