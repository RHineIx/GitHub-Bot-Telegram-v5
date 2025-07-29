# src/core/database.py

import asyncio
import logging
from typing import Optional, List, Any

import asyncpg
from cryptography.fernet import Fernet

from src.core.config import Settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages all persistent data using an asynchronous PostgreSQL database."""

    def __init__(self, settings: Settings):
        self.database_url = settings.database_url
        self._pool: Optional[asyncpg.Pool] = None
        self._write_lock = asyncio.Lock()
        self._cipher = Fernet(settings.encryption_key.encode())

    async def init_db(self) -> None:
        if self._pool:
            return
        try:
            self._pool = await asyncpg.create_pool(self.database_url, timeout=30)
            async with self._pool.acquire() as connection:
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS destinations (
                        target_id TEXT PRIMARY KEY
                    );
                    CREATE TABLE IF NOT EXISTS tracked_list (
                        list_slug TEXT PRIMARY KEY
                    );
                    CREATE TABLE IF NOT EXISTS repository_release_state (
                        repo_full_name TEXT PRIMARY KEY,
                        latest_release_node_id TEXT NOT NULL,
                        last_checked_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS release_destinations (
                        target_id TEXT PRIMARY KEY
                    );
                """)
            logger.info("Database initialized and connection pool established with PostgreSQL.")
        except Exception as e:
            logger.error(f"Failed to initialize database pool: {e}", exc_info=True)
            raise

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("Database connection pool closed.")

    async def _execute_write_query(self, query: str, *args) -> Optional[str]:
        async with self._write_lock:
            async with self._pool.acquire() as conn:
                return await conn.execute(query, *args)

    async def _set_state_value(self, key: str, value: Any) -> None:
        query = """
            INSERT INTO bot_state (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
        """
        await self._execute_write_query(query, key, str(value))

    async def _get_state_value(self, key: str) -> Optional[str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM bot_state WHERE key = $1", key)
            return row['value'] if row else None

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
        await self._execute_write_query("DELETE FROM bot_state WHERE key = $1", "github_token")
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
        await self._set_state_value("ai_summary_enabled", "1" if enabled else "0")

    async def is_ai_summary_enabled(self) -> bool:
        enabled_state = await self._get_state_value("ai_summary_enabled")
        return enabled_state != "0"

    async def set_ai_media_selection_enabled(self, enabled: bool) -> None:
        await self._set_state_value("ai_media_selection_enabled", "1" if enabled else "0")

    async def is_ai_media_selection_enabled(self) -> bool:
        enabled_state = await self._get_state_value("ai_media_selection_enabled")
        return enabled_state != "0"

    async def add_destination(self, target_id: str) -> None:
        query = "INSERT INTO destinations (target_id) VALUES ($1) ON CONFLICT (target_id) DO NOTHING"
        await self._execute_write_query(query, target_id)

    async def remove_destination(self, target_id: str) -> int:
        result_str = await self._execute_write_query("DELETE FROM destinations WHERE target_id = $1", target_id)
        return int(result_str.split(" ")[1]) if result_str and "DELETE" in result_str else 0

    async def get_all_destinations(self) -> List[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT target_id FROM destinations")
            return [row['target_id'] for row in rows]
    
    async def add_release_destination(self, target_id: str) -> None:
        query = "INSERT INTO release_destinations (target_id) VALUES ($1) ON CONFLICT (target_id) DO NOTHING"
        await self._execute_write_query(query, target_id)

    async def remove_release_destination(self, target_id: str) -> int:
        result_str = await self._execute_write_query("DELETE FROM release_destinations WHERE target_id = $1", target_id)
        return int(result_str.split(" ")[1]) if result_str and "DELETE" in result_str else 0

    async def get_all_release_destinations(self) -> List[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT target_id FROM release_destinations")
            return [row['target_id'] for row in rows]

    async def set_tracked_list(self, list_slug: str) -> None:
        async with self._write_lock:
             async with self._pool.acquire() as conn:
                await conn.execute("DELETE FROM tracked_list")
                await conn.execute("INSERT INTO tracked_list (list_slug) VALUES ($1)", list_slug)
        logger.info(f"Set tracked GitHub List to: {list_slug}")

    async def get_tracked_list(self) -> Optional[str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT list_slug FROM tracked_list")
            return row['list_slug'] if row else None

    async def clear_tracked_list(self) -> None:
        await self._execute_write_query("DELETE FROM tracked_list")
        logger.info("Cleared tracked GitHub list configuration.")

    async def get_repository_release_id(self, repo_full_name: str) -> Optional[str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT latest_release_node_id FROM repository_release_state WHERE repo_full_name = $1",
                repo_full_name,
            )
            return row['latest_release_node_id'] if row else None

    async def update_repository_release_id(self, repo_full_name: str, node_id: str) -> None:
        query = """
            INSERT INTO repository_release_state (repo_full_name, latest_release_node_id, last_checked_at)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT(repo_full_name) DO UPDATE SET
                latest_release_node_id = EXCLUDED.latest_release_node_id,
                last_checked_at = EXCLUDED.last_checked_at
        """
        await self._execute_write_query(query, repo_full_name, node_id)

    async def clear_release_states(self) -> None:
        await self._execute_write_query("DELETE FROM repository_release_state")
        logger.info("Cleared all repository release states.")
