# src/rhineix_github_bot/modules/telegram/handlers/command_handlers.py

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from rhineix_github_bot.core.config import Settings
from rhineix_github_bot.core.database import DatabaseManager
from rhineix_github_bot.modules.github.api import GitHubAPI, GitHubAPIError
from rhineix_github_bot.modules.jobs.scheduler import DigestScheduler
from rhineix_github_bot.modules.telegram.filters import IsOwnerFilter
from rhineix_github_bot.modules.telegram.keyboards import (
    get_remove_token_keyboard,
    get_settings_menu_keyboard,
)
from rhineix_github_bot.utils import format_duration, format_time_ago

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(IsOwnerFilter())


class TokenState(StatesGroup):
    waiting_for_token = State()


@router.message(CommandStart())
async def handle_start(message: types.Message):
    help_text = (
        f"👋 **Hi, {message.from_user.first_name}!**\n\n"
        "This bot monitors your GitHub starred repositories.\n\n"
        "📖 **Available Commands**\n\n"
        "**Core & Status:**\n"
        "`/status` - Shows a detailed summary of the bot's current status.\n"
        "`/settings` - Opens the interactive menu to configure the bot.\n\n"
        "**Token Management:**\n"
        "`/settoken` - Saves your GitHub Personal Access Token.\n"
        "`/removetoken` - Deletes your currently stored token.\n\n"
        "**Destination Management:**\n"
        "`/add_dest <ID>` - Adds a channel/group/topic ID for notifications.\n"
        "`/remove_dest <ID|me>` - Removes a notification destination.\n"
        "`/list_dests` - Lists all configured destinations."
    )
    await message.answer(help_text, parse_mode="Markdown")


@router.message(Command("settings"))
async def handle_settings(message: types.Message, db_manager: DatabaseManager):
    keyboard = await get_settings_menu_keyboard(db_manager)
    await message.answer("⚙️ Bot Settings", reply_markup=keyboard.as_markup())


@router.message(Command("status"))
async def handle_status(
    message: types.Message,
    db_manager: DatabaseManager,
    github_api: GitHubAPI,
    settings: Settings,
    start_time: datetime,
    scheduler: DigestScheduler,
):
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return
    wait_msg = await message.answer("🔍 Fetching status...")
    try:
        tasks = {
            "rate_limit_data": github_api.get_rate_limit(),
            "viewer_login": github_api.get_viewer_login(),
            "destinations": db_manager.get_all_destinations(),
            "is_paused": db_manager.is_monitoring_paused(),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        res = {
            key: val
            for key, val in zip(tasks.keys(), results)
            if not isinstance(val, Exception)
        }
        uptime = datetime.now(timezone.utc) - start_time
        uptime_str = str(uptime - timedelta(microseconds=uptime.microseconds))
        status_lines = [f"📊 **Bot Status**\n\n🕒 *Uptime:* `{uptime_str}`"]
        if login := res.get("viewer_login"):
            status_lines.append(f"👤 *GitHub Account:* `@{login}`")
        if rate_limit_data := res.get("rate_limit_data"):
            if rate_limit := rate_limit_data.rate_limit:
                reset_time = format_time_ago(rate_limit.reset_at.isoformat())
                status_lines.append(
                    f"📈 *API Limit:* `{rate_limit.remaining}/{rate_limit.limit}` (resets {reset_time})"
                )
        monitoring_status = "Paused ⏸️" if res.get("is_paused") else "Active ✅"
        status_lines.append(f"📢 *Monitoring:* `{monitoring_status}`")
        ai_status = "Enabled" if settings.gemini_api_key else "Disabled (No API Key)"
        if settings.gemini_api_key:
            db_ai_status = "Active ✅" if await db_manager.are_ai_features_enabled() else "Inactive ❌"
            ai_status = f"Enabled ({db_ai_status})"
        status_lines.append(f"🤖 *AI Features:* `{ai_status}`")
        if next_run := scheduler.get_next_run_time():
            status_lines.append(
                f"🗓️ *Next Digest Job:* {format_time_ago(next_run.isoformat())}"
            )
        stars_interval = (
            res.get("stars_interval") or settings.default_stars_monitor_interval
        )
        status_lines.extend(
            [
                f"⭐ *Stars Interval:* `{format_duration(stars_interval)}`",
                f"🧠 *AI Model:* `{settings.gemini_model_name}`",
                f"📍 *Destinations:* `{len(res.get('destinations', []))}` configured.",
            ]
        )
        await wait_msg.edit_text("\n".join(status_lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching status: {e}", exc_info=True)
        await wait_msg.edit_text("❌ An error occurred while fetching status.")


@router.message(Command("settoken"))
async def handle_set_token_command(message: types.Message, state: FSMContext):
    await message.answer(
        "Please send your GitHub Personal Access Token.\n\nYour token will be encrypted and stored securely. This message and your reply will be deleted automatically."
    )
    await state.set_state(TokenState.waiting_for_token)


@router.message(TokenState.waiting_for_token, F.text)
async def process_token(
    message: types.Message,
    state: FSMContext,
    db_manager: DatabaseManager,
    github_api: GitHubAPI,
):
    """Receives, validates, and stores the user's token."""
    token = message.text.strip()
    wait_msg = await message.answer("Validating token...")

    # Store the token first, so the API client can use it
    await db_manager.store_token(token)
    try:
        # Use the new validation method
        username = await github_api.get_viewer_login()
        if not username:
            raise GitHubAPIError(401, "Invalid token or missing permissions.")

        await db_manager.set_monitoring_paused(False)
        reply_text = f"✅ **Token validated!**\n\nConnected to: `@{username}`.\nMonitoring is now active."
        await wait_msg.edit_text(reply_text, parse_mode="Markdown")

    except GitHubAPIError:
        await db_manager.remove_token()
        await wait_msg.edit_text(
            "❌ **Invalid Token.** Please ensure it has the correct permissions and is not expired."
        )
    finally:
        await state.clear()
        try:
            await message.delete()
        except Exception:
            logger.warning("Could not delete user's token message.")


@router.message(Command("removetoken"))
async def handle_remove_token(message: types.Message):
    keyboard = get_remove_token_keyboard()
    await message.answer(
        "⚠️ **Are you sure?**\n\nThis will stop all monitoring.",
        parse_mode="Markdown",
        reply_markup=keyboard.as_markup(),
    )


@router.message(Command("add_dest"))
async def handle_add_destination(
    message: types.Message,
    command: CommandObject,
    bot: Bot,
    db_manager: DatabaseManager,
):
    if not command.args:
        await message.answer(
            "Usage: `/add_dest <ID>`\nExample: `/add_dest -100123456789`",
            parse_mode="Markdown",
        )
        return
    target_id, wait_msg = command.args, await message.answer(
        f"Verifying destination `{command.args}`..."
    )
    try:
        chat_id_str, thread_id = (
            (target_id.split("/")[0], int(target_id.split("/")[1]))
            if "/" in target_id
            else (target_id, None)
        )
        test_msg = await bot.send_message(
            chat_id_str, "✅ Verification successful.", message_thread_id=thread_id
        )
        await bot.delete_message(chat_id_str, test_msg.message_id)
        await db_manager.add_destination(target_id)
        await wait_msg.edit_text(
            f"✅ Destination `{target_id}` added successfully!", parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to verify destination {target_id}: {e}")
        await wait_msg.edit_text(
            "❌ **Failed to add destination.**\n\nPlease ensure the bot is a member of the chat and has permission to send messages.",
            parse_mode="Markdown",
        )


@router.message(Command("remove_dest"))
async def handle_remove_destination(
    message: types.Message, command: CommandObject, db_manager: DatabaseManager
):
    if not command.args:
        await message.answer("Usage: `/remove_dest <ID|me>`", parse_mode="Markdown")
        return
    target_id = (
        str(message.from_user.id) if command.args.lower() == "me" else command.args
    )
    if await db_manager.remove_destination(target_id) > 0:
        await message.answer(
            f"✅ Destination `{target_id}` removed.", parse_mode="Markdown"
        )
    else:
        await message.answer(
            f"❌ Destination `{target_id}` not found.", parse_mode="Markdown"
        )


@router.message(Command("list_dests"))
async def handle_list_destinations(message: types.Message, db_manager: DatabaseManager):
    if not (destinations := await db_manager.get_all_destinations()):
        await message.answer("There are no notification destinations configured.")
        return
    text = "📍 **Configured Destinations:**\n\n" + "\n".join(
        [f"`{dest}`" for dest in destinations]
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("testlog"))
async def handle_test_log(message: types.Message, settings: Settings):
    if not settings.log_channel_id:
        await message.answer("Log channel is not configured.")
        return
    try:
        logger.error("This is a test error message sent via the /testlog command.")
        await message.answer("✅ A test error log has been sent to the log channel.")
    except Exception as e:
        await message.answer(f"❌ Failed to send test log. Error: {e}")
