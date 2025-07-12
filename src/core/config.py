# src/core/config.py

import logging
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

env_path = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    """
    Manages all application settings for the GraphQL version of the bot.
    """

    # --- Telegram & Owner ---
    bot_token: str = Field(..., validation_alias="BOT_TOKEN")
    owner_user_id: int = Field(..., validation_alias="OWNER_USER_ID")
    log_channel_id: str | None = Field(default=None, validation_alias="LOG_CHANNEL_ID")

    # --- Gemini AI Settings ---
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model_name: str = Field(
        default="gemini-1.5-flash", validation_alias="GEMINI_MODEL_NAME"
    )

    # --- GitHub API Settings ---
    github_graphql_api: str = "https://api.github.com/graphql"
    github_api_base: str = "https://api.github.com"  # Kept for REST API calls

    # --- Bot Behavior ---
    parse_mode: str = "HTML"
    # Increased timeout to 60 seconds to better handle slow web scraping operations.
    request_timeout: int = 60
    default_stars_monitor_interval: int = 600
    default_release_monitor_interval: int = 3600

    model_config = SettingsConfigDict(
        env_file=env_path, env_file_encoding="utf-8", extra="ignore"
    )


try:
    settings = Settings()
except Exception as e:
    logger.critical(f"FATAL: Failed to load settings. Error: {e}")
    raise