# src/modules/telegram/handlers/command_handlers.py

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from src.core.config import Settings
from src.core.database import DatabaseManager
from src.modules.github.api import GitHubAPI, GitHubAPIError
from src.modules.jobs.scheduler import DigestScheduler
from src.modules.telegram.filters import IsOwnerFilter
from src.modules.telegram.keyboards import (
    get_remove_token_keyboard,
    get_settings_menu_keyboard,
    get_tracking_lists_keyboard,
)
from src.utils import format_time_ago

logger = logging.getLogger(__name__)
router = Router()
router.message.filter(IsOwnerFilter())


class TokenState(StatesGroup):
    waiting_for_token = State()


@router.message(CommandStart())
async def handle_start(message: types.Message):
    help_text = (
        f"👋 **Hi, {message.from_user.first_name}!**\n\n"
        "📖 **Available Commands**\n\n"
        "**Core & Status:**\n"
        "`/status` - Shows a detailed summary of the bot's current status.\n"
        "`/settings` - Opens the interactive menu to configure the bot.\n"
        "`/track` - Configure tracking for new releases from a GitHub List.\n\n"
        "**Token Management:**\n"
        "`/settoken` - Saves your GitHub Personal Access Token.\n"
        "`/removetoken` - Deletes your currently stored token.\n\n"
        "**Destination Management (Stars):**\n"
        "`/add_dest <ID>` - Adds a channel/group for star notifications.\n"
        "`/remove_dest <ID|me>` - Removes a star notification destination.\n"
        "`/list_dests` - Lists all configured star destinations.\n\n"
        "**Destination Management (Releases):**\n"
        "`/add_dest_rels <ID>` - Adds a destination for new releases.\n"
        "`/remove_dest_rels <ID|me>` - Removes a release destination.\n"
        "`/list_dest_rels` - Lists all release destinations."
    )
    await message.answer(help_text, parse_mode="Markdown", message_effect_id="5046509860389126442")


@router.message(Command("settings"))
async def handle_settings(message: types.Message, db_manager: DatabaseManager):
    keyboard = await get_settings_menu_keyboard(db_manager)
    await message.answer("⚙️ Bot Settings\n", reply_markup=keyboard.as_markup())


@router.message(Command("status"))
async def handle_status(
    message: types.Message,
    db_manager: DatabaseManager,
    github_api: GitHubAPI,
    settings: Settings,
    start_time: datetime,
    scheduler: DigestScheduler,
):
    # This command already has the token check, which is great.
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return
    
    wait_msg = await message.answer("🔍 Fetching status...")
    try:
        tasks = {
            "rate_limit_data": github_api.get_rate_limit(),
            "viewer_login": github_api.get_viewer_login(),
            "destinations": db_manager.get_all_destinations(),
            "release_dests": db_manager.get_all_release_destinations(),
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
            db_ai_status = (
                "Active ✅"
                if await db_manager.are_ai_features_enabled()
                else "Inactive ❌"
            )
            ai_status = f"Enabled ({db_ai_status})"
        status_lines.append(f"🤖 *AI Features:* `{ai_status}`")
        if next_run := scheduler.get_next_run_time():
            status_lines.append(
                f"🗓️ *Next Digest Job:* {format_time_ago(next_run.isoformat())}"
            )
        status_lines.extend(
            [
                f"⭐ *Star Destinations:* `{len(res.get('destinations', []))}` configured.",
                f"🚀 *Release Destinations:* `{len(res.get('release_dests', []))}` configured.",
            ]
        )
        await wait_msg.edit_text("\n".join(status_lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching status: {e}", exc_info=True)
        await wait_msg.edit_text("❌ An error occurred while fetching status.")


@router.message(Command("settoken"))
async def handle_set_token(message: types.Message, state: FSMContext):
    """Prompts the user to send their GitHub token."""
    await message.answer(
        "Please send your GitHub Personal Access Token.\n\n"
        "The token needs the **`read:user`** and **`repo`** scopes.\n"
        "Your token will be encrypted and stored securely.\n\n"
        "To maximize security, your message with the token will be deleted after processing.", parse_mode="Markdown"
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

    # Store the token temporarily for validation
    await db_manager.store_token(token)
    try:
        username = await github_api.get_viewer_login()
        if not username:
            raise GitHubAPIError(401, "Invalid token or missing permissions.")

        user_chat_id = str(message.from_user.id)
        # Add the user's chat as a destination for BOTH notification types
        await db_manager.add_destination(user_chat_id)
        await db_manager.add_release_destination(user_chat_id)
        logger.info(f"Automatically added {user_chat_id} as a default destination for all notification types.")

        await db_manager.set_monitoring_paused(False)
        reply_text = (
            f"✅ **Token validated!**\n\n"
            f"Connected to: `@{username}`.\n"
            f"Monitoring is now active, and this chat has been set as the default destination for all notifications."
        )
        await wait_msg.edit_text(reply_text, parse_mode="Markdown")

    except GitHubAPIError:
        await db_manager.remove_token() # Clean up the invalid token
        await wait_msg.edit_text(
            "❌ **Invalid Token.** Please ensure it has the correct permissions (read:user, repo) and is not expired."
        )
    finally:
        await state.clear()
        try:
            # Delete the message containing the user's token for security
            await message.delete()
        except Exception:
            logger.warning("Could not delete user's token message.")


@router.message(Command("removetoken"))
async def handle_remove_token(message: types.Message, db_manager: DatabaseManager):
    # ADDED: Token check
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return

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
    # ADDED: Token check
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return

    if not command.args:
        await message.answer("Usage: `/add_dest <ID>`")
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
            f"✅ Star destination `{target_id}` added successfully!", parse_mode="Markdown"
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
    # ADDED: Token check
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return

    if not command.args:
        await message.answer("Usage: `/remove_dest <ID|me>`")
        return
    target_id = (
        str(message.from_user.id) if command.args.lower() == "me" else command.args
    )
    if await db_manager.remove_destination(target_id) > 0:
        await message.answer(f"✅ Star destination `{target_id}` removed.")
    else:
        await message.answer(f"❌ Star destination `{target_id}` not found.")


@router.message(Command("list_dests"))
async def handle_list_destinations(message: types.Message, db_manager: DatabaseManager):
    # ADDED: Token check
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return

    if not (destinations := await db_manager.get_all_destinations()):
        await message.answer("There are no star notification destinations configured.")
        return
    text = "📍 **Configured Star Destinations:**\n\n" + "\n".join(
        [f"`{dest}`" for dest in destinations]
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("add_dest_rels"))
async def handle_add_release_destination(
    message: types.Message,
    command: CommandObject,
    bot: Bot,
    db_manager: DatabaseManager,
):
    """Adds a destination for release notifications."""
    # ADDED: Token check
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return

    if not command.args:
        await message.answer("Usage: `/add_dest_rels <ID>`")
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
        await db_manager.add_release_destination(target_id)
        await wait_msg.edit_text(
            f"✅ Release destination `{target_id}` added successfully!", parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to verify destination {target_id}: {e}")
        await wait_msg.edit_text(
            "❌ **Failed to add destination.**", parse_mode="Markdown"
        )

@router.message(Command("remove_dest_rels"))
async def handle_remove_release_destination(
    message: types.Message, command: CommandObject, db_manager: DatabaseManager
):
    """Removes a destination for release notifications."""
    # ADDED: Token check
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return

    if not command.args:
        await message.answer("Usage: `/remove_dest_rels <ID|me>`")
        return
    target_id = (
        str(message.from_user.id) if command.args.lower() == "me" else command.args
    )
    if await db_manager.remove_release_destination(target_id) > 0:
        await message.answer(f"✅ Release destination `{target_id}` removed.")
    else:
        await message.answer(f"❌ Release destination `{target_id}` not found.")

@router.message(Command("list_dest_rels"))
async def handle_list_release_destinations(message: types.Message, db_manager: DatabaseManager):
    """Lists all configured release destinations."""
    # ADDED: Token check
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return

    if not (destinations := await db_manager.get_all_release_destinations()):
        await message.answer("There are no release notification destinations configured.")
        return
    text = "📍 **Configured Release Destinations:**\n\n" + "\n".join(
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


@router.message(Command("track"))
async def handle_track_command(message: types.Message, github_api: GitHubAPI, db_manager: DatabaseManager):
    """Displays the menu for selecting a GitHub List to track for releases."""
    # ADDED: Token check
    if not await db_manager.token_exists():
        await message.answer("❌ No GitHub token is set. Use `/settoken` to add one.")
        return

    wait_msg = await message.answer("🔍 Fetching your GitHub Lists...")
    lists_data = await github_api.get_user_repository_lists()
    if lists_data and lists_data.lists.edges:
        repo_lists = [edge.node for edge in lists_data.lists.edges]
        keyboard = get_tracking_lists_keyboard(repo_lists)
        await wait_msg.edit_text(
            "**Track Releases from a List**\n\n"
            "Select a GitHub List below. The bot will monitor all repositories in that list and notify you of any new releases.",
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
    else:
        await wait_msg.edit_text(
            "**No GitHub Lists Found**\n\n"
            "You don't seem to have any Lists on your GitHub Stars page. Create one first, then run this command again.\n\n"
            "You can create a list by going to your Stars, clicking the 'Lists' tab, and then 'Create list'.",
            parse_mode="Markdown"
        )