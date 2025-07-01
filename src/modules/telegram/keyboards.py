# src/modules/telegram/keyboards.py
import asyncio
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.core.config import Settings
from src.core.database import DatabaseManager
from aiogram.filters.callback_data import CallbackData
from src.modules.github.models import RepositoryList


def cb_factory(action: str, value: str = "") -> str:
    """Creates a standardized callback data string for settings."""
    return f"cb:{action}:{value}"


async def get_settings_menu_keyboard(db: DatabaseManager) -> InlineKeyboardBuilder:
    """Builds the main settings menu keyboard."""
    builder = InlineKeyboardBuilder()
    is_paused, digest_mode, ai_enabled = await asyncio.gather(
        db.is_monitoring_paused(), db.get_digest_mode(), db.are_ai_features_enabled()
    )
    builder.button(
        text="▶️ Resume" if is_paused else "⏸️ Pause",
        callback_data=cb_factory("toggle_pause"),
    )
    builder.button(
        text=f"🔔 Mode: {digest_mode.capitalize()}",
        callback_data=cb_factory("open_digest_menu"),
    )
    builder.button(
        text=f"🧠 AI: {'ON' if ai_enabled else 'OFF'}",
        callback_data=cb_factory("toggle_ai"),
    )
    builder.button(text="⚙️ Intervals", callback_data=cb_factory("open_interval_menu"))
    builder.button(text="❌ Close Menu", callback_data=cb_factory("close"))
    builder.adjust(2, 2, 1)
    return builder


async def get_digest_submenu_keyboard(db: DatabaseManager) -> InlineKeyboardBuilder:
    """Builds the digest mode selection submenu keyboard."""
    builder = InlineKeyboardBuilder()
    current_mode = await db.get_digest_mode()
    modes = ["off", "daily", "weekly"]

    for mode in modes:
        text = f"✅ {mode.capitalize()}" if mode == current_mode else mode.capitalize()
        builder.button(text=text, callback_data=cb_factory("set_digest_mode", mode))

    builder.button(text="⬅️ Back", callback_data=cb_factory("main_menu"))
    builder.adjust(3, 1)
    return builder


async def get_interval_submenu_keyboard(
    db: DatabaseManager, settings: Settings
) -> InlineKeyboardBuilder:
    """Builds the star monitoring interval selection submenu keyboard."""
    builder = InlineKeyboardBuilder()
    current_interval = (
        await db.get_stars_monitor_interval() or settings.default_stars_monitor_interval
    )
    intervals = [
        ("1 minute", 60),
        ("10 minutes", 600),
        ("30 minutes", 1800),
        ("1 hour", 3600),
        ("6 hours", 21600),
        ("12 hours", 43200),
    ]

    for label, seconds in intervals:
        text = f"✅ {label}" if seconds == current_interval else label
        builder.button(
            text=text, callback_data=cb_factory("set_stars_interval", str(seconds))
        )

    builder.button(text="⬅️ Back", callback_data=cb_factory("main_menu"))
    builder.adjust(2, 2, 2, 1)
    return builder


def get_remove_token_keyboard() -> InlineKeyboardBuilder:
    """Builds the confirmation keyboard for token removal."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Yes, remove it", callback_data=cb_factory("confirm_remove_token")
    )
    builder.button(text="❌ Cancel", callback_data=cb_factory("cancel_action"))
    return builder

class TrackingCallback(CallbackData, prefix="track"):
    """CallbackData factory for release tracking actions."""
    action: str
    value: str | None = None


def get_tracking_lists_keyboard(lists: list[RepositoryList]) -> InlineKeyboardBuilder:
    """Builds the keyboard for selecting a GitHub List to track."""
    builder = InlineKeyboardBuilder()

    for repo_list in lists:
        # Go back to using the slug, as it's the filter key
        builder.button(
            text=f"📝 {repo_list.name}",
            callback_data=TrackingCallback(action="set_list", value=repo_list.slug).pack(),
        )
    # ... (rest of the function is the same) ...
    builder.button(
        text="❌ Stop Tracking",
        callback_data=TrackingCallback(action="stop", value="all").pack(),
    )
    builder.button(
        text="⬅️ Close",
        callback_data=cb_factory("close"),
    )
    builder.adjust(1)
    return builder