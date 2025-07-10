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

# --- NEW PRIVATE HELPER to format seconds into m, h, d ---
def _format_seconds_to_short_str(seconds: int) -> str:
    """Formats a duration in seconds into a short, readable string like '10m', '1h', '2d'."""
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"

async def get_settings_menu_keyboard(db: DatabaseManager) -> InlineKeyboardBuilder:
    """Builds the main settings menu keyboard."""
    builder = InlineKeyboardBuilder()
    is_paused, digest_mode = await asyncio.gather(
        db.is_monitoring_paused(), db.get_digest_mode()
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
        text="🤖 AI Settings",
        callback_data=cb_factory("open_ai_menu"),
    )
    builder.button(
        text="⏱️ Intervals",
        callback_data=cb_factory("open_intervals_menu"),
    )

    builder.button(text="❌ Close Menu", callback_data=cb_factory("close"))
    builder.adjust(2) # A clean 2-column layout
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


async def get_ai_submenu_keyboard(db: DatabaseManager) -> InlineKeyboardBuilder:
    """Builds the AI feature selection submenu keyboard."""
    builder = InlineKeyboardBuilder()
    summary_on, media_on = await asyncio.gather(
        db.is_ai_summary_enabled(), db.is_ai_media_selection_enabled()
    )

    builder.button(
        text=f"📝 AI Summary: {'ON' if summary_on else 'OFF'}",
        callback_data=cb_factory("toggle_ai_summary"),
    )
    builder.button(
        text=f"🖼️ AI Media Select: {'ON' if media_on else 'OFF'}",
        callback_data=cb_factory("toggle_ai_media"),
    )
    builder.button(text="⬅️ Back", callback_data=cb_factory("main_menu"))
    builder.adjust(1, 1, 1)
    return builder

async def get_intervals_submenu_keyboard(db: DatabaseManager, settings: Settings) -> InlineKeyboardBuilder:
    """Builds the interval settings hub, showing current values."""
    builder = InlineKeyboardBuilder()
    stars_interval, release_interval = await asyncio.gather(
        db.get_stars_monitor_interval(),
        db.get_release_monitor_interval(),
    )

    # Use defaults from settings if not set in DB
    stars_interval = stars_interval or settings.default_stars_monitor_interval
    release_interval = release_interval or settings.default_release_monitor_interval

    # Format the current values for display
    stars_str = _format_seconds_to_short_str(stars_interval)
    release_str = _format_seconds_to_short_str(release_interval)

    builder.button(
        text=f"⚙️ Stars Interval: {stars_str}",
        callback_data=cb_factory("open_interval_menu")
    )
    builder.button(
        text=f"🚀 Release Interval: {release_str}",
        callback_data=cb_factory("open_release_menu")
    )
    builder.button(text="⬅️ Back", callback_data=cb_factory("main_menu"))
    builder.adjust(1)
    return builder

def _get_generic_interval_keyboard(
    current_interval: int, 
    intervals: list[tuple[str, int]], 
    callback_action: str
) -> InlineKeyboardBuilder:
    """Builds a generic interval selection keyboard."""
    builder = InlineKeyboardBuilder()
    for label, seconds in intervals:
        text = f"✅ {label}" if seconds == current_interval else label
        builder.button(
            text=text, callback_data=cb_factory(callback_action, str(seconds))
        )
    builder.button(text="⬅️ Back", callback_data=cb_factory("open_intervals_menu"))
    builder.adjust(2, 2, 2, 2, 1)
    return builder

async def get_interval_submenu_keyboard(
    db: DatabaseManager, settings: Settings
) -> InlineKeyboardBuilder:
    """Builds the star monitoring interval selection submenu keyboard."""
    current_interval = (
        await db.get_stars_monitor_interval() or settings.default_stars_monitor_interval
    )
    intervals = [
        ("1 minute", 60), ("10 minutes", 600), ("30 minutes", 1800), ("1 hour", 3600),
        ("3 hours", 10800), ("6 hours", 21600), ("12 hours", 43200), ("1 day", 86400),
    ]
    return _get_generic_interval_keyboard(current_interval, intervals, "set_stars_interval")

async def get_release_interval_submenu_keyboard(
    db: DatabaseManager, settings: Settings
) -> InlineKeyboardBuilder:
    """Builds the release monitoring interval selection submenu keyboard."""
    current_interval = (
        await db.get_release_monitor_interval() or settings.default_release_monitor_interval
    )
    intervals = [
        ("30 minutes", 1800), ("1 hour", 3600), ("3 hours", 10800),
        ("6 hours", 21600), ("12 hours", 43200), ("1 day", 86400),
    ]
    return _get_generic_interval_keyboard(current_interval, intervals, "set_release_interval")


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
        builder.button(
            text=f"📝 {repo_list.name}",
            callback_data=TrackingCallback(action="set_list", value=repo_list.slug).pack(),
        )
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


def get_view_on_github_keyboard(url: str) -> InlineKeyboardBuilder:
    """Builds a simple keyboard with a single 'View on GitHub' URL button."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 View on GitHub", url=url)
    return builder