# src/rhineix_github_bot/modules/telegram/handlers/settings_handlers.py

import logging
from aiogram import Router, F, types

from rhineix_github_bot.core.config import Settings
from rhineix_github_bot.core.database import DatabaseManager
from rhineix_github_bot.modules.jobs.monitor import RepositoryMonitor
from rhineix_github_bot.modules.telegram.keyboards import (
    get_digest_submenu_keyboard,
    get_interval_submenu_keyboard,
    get_settings_menu_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()

async def _edit_to_main_menu(message: types.Message, db_manager: DatabaseManager):
    """Helper function to edit a message to show the main settings menu."""
    keyboard = await get_settings_menu_keyboard(db_manager)
    await message.edit_text("⚙️ Bot Settings", reply_markup=keyboard.as_markup())

@router.callback_query(F.data.startswith("cb:"))
async def handle_settings_callback(
    call: types.CallbackQuery, db_manager: DatabaseManager, monitor: RepositoryMonitor, settings: Settings,
):
    await call.answer()
    try: _, action, value = call.data.split(":", 2)
    except ValueError:
        logger.warning(f"Received malformed callback data: {call.data}")
        return

    if action == "toggle_pause":
        await db_manager.set_monitoring_paused(not await db_manager.is_monitoring_paused())
        await _edit_to_main_menu(call.message, db_manager)
    elif action == "toggle_ai":
        await db_manager.set_ai_features_enabled(not await db_manager.are_ai_features_enabled())
        await _edit_to_main_menu(call.message, db_manager)
    elif action == "main_menu":
        await _edit_to_main_menu(call.message, db_manager)
    elif action == "open_digest_menu":
        keyboard = await get_digest_submenu_keyboard(db_manager)
        await call.message.edit_text("🔔 Select Notification Mode:", reply_markup=keyboard.as_markup())
    elif action == "open_interval_menu":
        keyboard = await get_interval_submenu_keyboard(db_manager, settings)
        await call.message.edit_text("⚙️ Select Stars Monitoring Interval:", reply_markup=keyboard.as_markup())
    elif action == "set_digest_mode":
        await db_manager.update_digest_mode(value)
        keyboard = await get_digest_submenu_keyboard(db_manager)
        await call.message.edit_text("🔔 Select Notification Mode:", reply_markup=keyboard.as_markup())
    elif action == "set_stars_interval":
        await db_manager.update_stars_monitor_interval(int(value))
        monitor.signal_settings_changed()
        keyboard = await get_interval_submenu_keyboard(db_manager, settings)
        await call.message.edit_text("⚙️ Select Stars Monitoring Interval:", reply_markup=keyboard.as_markup())
    elif action == "confirm_remove_token":
        await db_manager.remove_token()
        await db_manager.set_monitoring_paused(True)
        await call.message.edit_text("🗑️ **Token Removed.** Monitoring has been paused.", parse_mode="Markdown", reply_markup=None)
    elif action == "cancel_action":
        await call.message.edit_text("Action cancelled.", reply_markup=None)
    elif action == "close":
        await call.message.delete()