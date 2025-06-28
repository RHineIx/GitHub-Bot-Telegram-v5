# tests/test_notification_service.py (Final Corrected Version)

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from aiogram import Bot

from src.modules.telegram.services.notification_service import NotificationService
from src.core.database import DatabaseManager
from src.modules.github.api import GitHubAPI
from src.modules.ai.summarizer import AISummarizer
from src.modules.github.models import (
    Repository,
    Owner,
    DefaultBranchRef,
    NotificationRepoData,
)


@pytest.fixture
def mock_dependencies(mocker):
    """Creates mock objects for all dependencies of NotificationService."""
    mock_bot = mocker.AsyncMock(spec=Bot)
    mock_db = mocker.AsyncMock(spec=DatabaseManager)
    mock_gh_api = mocker.AsyncMock(spec=GitHubAPI)
    mock_summarizer = mocker.AsyncMock(spec=AISummarizer)

    return {
        "bot": mock_bot,
        "db_manager": mock_db,
        "github_api": mock_gh_api,
        "summarizer": mock_summarizer,
    }


@pytest.mark.asyncio
class TestNotificationService:

    async def test_sends_media_when_ai_succeeds(self, mock_dependencies, mocker):
        """
        Tests the ideal scenario: AI is enabled and successfully finds media.
        """
        # 1. ARRANGE

        # --- THE FIX: All keyword arguments now use the camelCase alias to match the model's validation expectation ---
        fake_repo = Repository(
            nameWithOwner="owner/repo",
            url="http://example.com/repo",
            description="A test repo",
            stargazerCount=10,
            forkCount=5,
            pushedAt=datetime.now(timezone.utc),
            owner=Owner(login="owner", avatarUrl="http://example.com/avatar.png"),
            defaultBranchRef=DefaultBranchRef(name="main"),
            latest_release=None,
            languages=None,
        )
        mock_dependencies[
            "github_api"
        ].get_repository_data_for_notification.return_value = NotificationRepoData(
            repository=fake_repo
        )

        mock_dependencies["github_api"].get_readme.return_value = (
            "Hello world! ![An image](./path/to/image.png)"
        )

        mock_dependencies["db_manager"].are_ai_features_enabled.return_value = True
        mock_dependencies["db_manager"].get_all_destinations.return_value = [
            "-100123456789"
        ]
        mock_dependencies["summarizer"].select_preview_media.return_value = [
            "http://example.com/image.png"
        ]

        mocker.patch(
            "src.modules.telegram.services.notification_service.get_media_info",
            return_value=("image/png", "http://example.com/image.png"),
        )

        # 2. ACT
        service = NotificationService(**mock_dependencies)
        await service.process_and_send("owner/repo")

        # 3. ASSERT
        mock_dependencies["bot"].send_photo.assert_awaited_once()
        mock_dependencies["bot"].send_message.assert_not_awaited()

    async def test_sends_fallback_when_ai_fails(self, mock_dependencies, mocker):
        """
        Tests the fallback scenario: AI is enabled but finds no media.
        """
        # 1. ARRANGE

        # --- THE FIX: All keyword arguments now use the camelCase alias ---
        fake_repo = Repository(
            nameWithOwner="owner/repo",
            url="http://example.com/repo",
            description="A test repo",
            stargazerCount=10,
            forkCount=5,
            pushedAt=datetime.now(timezone.utc),
            owner=Owner(login="owner", avatarUrl="http://example.com/avatar.png"),
            defaultBranchRef=DefaultBranchRef(name="main"),
            latest_release=None,
            languages=None,
        )
        mock_dependencies[
            "github_api"
        ].get_repository_data_for_notification.return_value = NotificationRepoData(
            repository=fake_repo
        )

        mock_dependencies["github_api"].get_readme.return_value = "Fake README content"

        mock_dependencies["db_manager"].are_ai_features_enabled.return_value = True
        mock_dependencies["db_manager"].get_all_destinations.return_value = [
            "-100123456789"
        ]

        mock_dependencies["summarizer"].select_preview_media.return_value = []

        mocker.patch(
            "src.modules.telegram.services.notification_service.scrape_social_preview_image",
            return_value=None,
        )

        # 2. ACT
        service = NotificationService(**mock_dependencies)
        await service.process_and_send("owner/repo")

        # 3. ASSERT
        mock_dependencies["bot"].send_photo.assert_not_awaited()
        mock_dependencies["bot"].send_message.assert_awaited_once()
