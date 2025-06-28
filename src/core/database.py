# src/core/database.py

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
            self._connection = await aiosqlite.connect(self.db_path)
            await self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS destinations (target_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS digest_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_full_name TEXT UNIQUE NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    async def update_last_check_timestamp(self, timestamp: str) -> None:
        await self._set_state_value("last_check_timestamp", timestamp)

    async def get_last_check_timestamp(self) -> Optional[str]:
        return await self._get_state_value("last_check_timestamp")

    async def set_ai_features_enabled(self, enabled: bool) -> None:
        await self._set_state_value("ai_features_enabled", "1" if enabled else "0")

    async def are_ai_features_enabled(self) -> bool:
        enabled_state = await self._get_state_value("ai_features_enabled")
        return enabled_state != "0"

    async def update_digest_mode(self, mode: str) -> None:
        await self._set_state_value("digest_mode", mode)
        logger.info(f"Digest mode set to: {mode}")

    async def get_digest_mode(self) -> str:
        mode = await self._get_state_value("digest_mode")
        return mode if mode else "off"

    async def add_destination(self, target_id: str) -> None:
        await self._connection.execute(
            "INSERT OR IGNORE INTO destinations (target_id) VALUES (?)", (target_id,)
        )
        await self._connection.commit()

    async def remove_destination(self, target_id: str) -> int:
        cursor = await self._connection.execute(
            "DELETE FROM destinations WHERE target_id = ?", (target_id,)
        )
        await self._connection.commit()
        return cursor.rowcount

    async def get_all_destinations(self) -> List[str]:
        cursor = await self._connection.execute("SELECT target_id FROM destinations")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def add_repo_to_digest(self, repo_full_name: str) -> None:
        await self._connection.execute(
            "INSERT OR IGNORE INTO digest_queue (repo_full_name) VALUES (?)",
            (repo_full_name,),
        )
        await self._connection.commit()

    async def get_and_clear_digest_queue(self) -> List[str]:
        cursor = await self._connection.execute(
            "SELECT repo_full_name FROM digest_queue ORDER BY added_at ASC"
        )
        repo_list = [row[0] for row in await cursor.fetchall()]
        if repo_list:
            await self._connection.execute("DELETE FROM digest_queue")
            await self._connection.commit()
        return repo_list

    async def get_digest_queue_count(self) -> int:
        cursor = await self._connection.execute("SELECT COUNT(*) FROM digest_queue")
        result = await cursor.fetchone()
        return result[0] if result else 0
