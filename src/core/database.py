# src/core/database.py

import asyncio
import logging
from typing import Optional, List, Any

import aiosqlite
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"
KEY_PATH = "bot_secret.key"


class DatabaseManager:
    """Manages all persistent data using an asynchronous SQLite database."""

    def __init__(self, db_path: str = DB_PATH, key_path: str = KEY_PATH):
        self.db_path = db_path
        self.key_path = key_path
        self._connection: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()  # Lock to serialize write operations
        self._encryption_key = self._get_or_create_key()
        self._cipher = Fernet(self._encryption_key)

    def _get_or_create_key(self) -> bytes:
        try:
            with open(self.key_path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            logger.info(f"Generating new encryption key at {self.key_path}")
            key = Fernet.generate_key()
            with open(self.key_path, "wb") as f:
                f.write(key)
            return key

    async def init_db(self) -> None:
        if self._connection:
            return
        try:
            # Set a timeout to reduce locking issues, though the lock is the main fix
            self._connection = await aiosqlite.connect(self.db_path, timeout=10)
            await self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS destinations (target_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS tracked_list (
                    list_slug TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS repository_release_state (
                    repo_full_name TEXT PRIMARY KEY,
                    latest_release_node_id TEXT NOT NULL,
                    last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS release_destinations (
                    target_id TEXT PRIMARY KEY
                );
                """
            )
            await self._connection.commit()
            logger.info("Database initialized and connection established.")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}", exc_info=True)
            if self._connection:
                await self._connection.close()
            raise

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
            logger.info("Database connection closed.")

    async def _set_state_value(self, key: str, value: Any) -> None:
        async with self._write_lock:
            await self._connection.execute(
                "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
            await self._connection.commit()

    async def _get_state_value(self, key: str) -> Optional[str]:
        cursor = await self._connection.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def store_token(self, token: str) -> None:
        encrypted_token = self._cipher.encrypt(token.encode()).decode()
        await self._set_state_value("github_token", encrypted_token)
        logger.info("GitHub token has been encrypted and stored.")

    async def get_token(self) -> Optional[str]:
        encrypted_token = await self._get_state_value("github_token")
        if encrypted_token:
            return self._cipher.decrypt(encrypted_token.encode()).decode()
        return None

    async def token_exists(self) -> bool:
        return await self._get_state_value("github_token") is not None

    async def remove_token(self) -> None:
        async with self._write_lock:
            await self._connection.execute(
                "DELETE FROM bot_state WHERE key = ?", ("github_token",)
            )
            await self._connection.commit()
        logger.info("GitHub token has been removed.")

    async def set_monitoring_paused(self, paused: bool) -> None:
        await self._set_state_value("monitoring_paused", "1" if paused else "0")

    async def is_monitoring_paused(self) -> bool:
        return await self._get_state_value("monitoring_paused") == "1"

    async def update_stars_monitor_interval(self, seconds: int) -> None:
        await self._set_state_value("stars_monitor_interval", seconds)
        logger.info(f"Stars monitor interval set to {seconds} seconds.")

    async def get_stars_monitor_interval(self) -> Optional[int]:
        interval = await self._get_state_value("stars_monitor_interval")
        return int(interval) if interval else None
    
    async def update_release_monitor_interval(self, seconds: int) -> None:
        await self._set_state_value("release_monitor_interval", seconds)
        logger.info(f"Release monitor interval set to {seconds} seconds.")

    async def get_release_monitor_interval(self) -> Optional[int]:
        interval = await self._get_state_value("release_monitor_interval")
        return int(interval) if interval else None

    async def update_last_check_timestamp(self, timestamp: str) -> None:
        await self._set_state_value("last_check_timestamp", timestamp)

    async def get_last_check_timestamp(self) -> Optional[str]:
        return await self._get_state_value("last_check_timestamp")

    async def set_ai_summary_enabled(self, enabled: bool) -> None:
        """Sets whether the AI summary feature is enabled."""
        await self._set_state_value("ai_summary_enabled", "1" if enabled else "0")

    async def is_ai_summary_enabled(self) -> bool:
        """Checks if the AI summary feature is enabled. Defaults to True if not set."""
        enabled_state = await self._get_state_value("ai_summary_enabled")
        return enabled_state != "0" # Default to ON

    async def set_ai_media_selection_enabled(self, enabled: bool) -> None:
        """Sets whether the AI media selection feature is enabled."""
        await self._set_state_value("ai_media_selection_enabled", "1" if enabled else "0")

    async def is_ai_media_selection_enabled(self) -> bool:
        """Checks if the AI media selection feature is enabled. Defaults to True if not set."""
        enabled_state = await self._get_state_value("ai_media_selection_enabled")
        return enabled_state != "0" # Default to ON

    async def add_destination(self, target_id: str) -> None:
        async with self._write_lock:
            await self._connection.execute(
                "INSERT OR IGNORE INTO destinations (target_id) VALUES (?)", (target_id,)
            )
            await self._connection.commit()

    async def remove_destination(self, target_id: str) -> int:
        async with self._write_lock:
            cursor = await self._connection.execute(
                "DELETE FROM destinations WHERE target_id = ?", (target_id,)
            )
            await self._connection.commit()
            return cursor.rowcount

    async def get_all_destinations(self) -> List[str]:
        cursor = await self._connection.execute("SELECT target_id FROM destinations")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]
    
    async def add_release_destination(self, target_id: str) -> None:
        async with self._write_lock:
            await self._connection.execute(
                "INSERT OR IGNORE INTO release_destinations (target_id) VALUES (?)", (target_id,)
            )
            await self._connection.commit()

    async def remove_release_destination(self, target_id: str) -> int:
        async with self._write_lock:
            cursor = await self._connection.execute(
                "DELETE FROM release_destinations WHERE target_id = ?", (target_id,)
            )
            await self._connection.commit()
            return cursor.rowcount

    async def get_all_release_destinations(self) -> List[str]:
        cursor = await self._connection.execute("SELECT target_id FROM release_destinations")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    # --- Methods for Release Tracking ---

    async def set_tracked_list(self, list_slug: str) -> None:
        """Sets the single GitHub List to be tracked, replacing any existing one."""
        async with self._write_lock:
            await self._connection.execute("DELETE FROM tracked_list")
            await self._connection.execute(
                "INSERT INTO tracked_list (list_slug) VALUES (?)", (list_slug,)
            )
            await self._connection.commit()
        logger.info(f"Set tracked GitHub List to: {list_slug}")

    async def get_tracked_list(self) -> Optional[str]:
            """Gets the slug of the currently tracked GitHub List."""
            cursor = await self._connection.execute("SELECT list_slug FROM tracked_list")
            row = await cursor.fetchone()
            return row[0] if row else None

    async def clear_tracked_list(self) -> None:
        """Removes the tracked GitHub List setting."""
        async with self._write_lock:
            await self._connection.execute("DELETE FROM tracked_list")
            await self._connection.commit()
        logger.info("Cleared tracked GitHub list configuration.")


    async def get_repository_release_id(self, repo_full_name: str) -> Optional[str]:
        """Gets the last known release node_id for a specific repository."""
        cursor = await self._connection.execute(
            "SELECT latest_release_node_id FROM repository_release_state WHERE repo_full_name = ?",
            (repo_full_name,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def update_repository_release_id(self, repo_full_name: str, node_id: str) -> None:
        """Adds or updates the latest known release node_id for a repository."""
        async with self._write_lock:
            await self._connection.execute(
                """
                INSERT INTO repository_release_state (repo_full_name, latest_release_node_id, last_checked_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(repo_full_name) DO UPDATE SET
                    latest_release_node_id = excluded.latest_release_node_id,
                    last_checked_at = excluded.last_checked_at
                """,
                (repo_full_name, node_id),
            )
            await self._connection.commit()

    async def clear_release_states(self) -> None:
        """Wipes all repository release states. Used when changing tracked lists."""
        async with self._write_lock:
            await self._connection.execute("DELETE FROM repository_release_state")
            await self._connection.commit()
        logger.info("Cleared all repository release states.")