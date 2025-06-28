# tests/test_utils.py (Updated)

import pytest
from aiohttp import ClientSession
from rhineix_github_bot.utils import format_duration, format_time_ago, get_media_info

# We can group related tests inside a class for better organization
class TestFormatDuration:
    
    def test_format_duration_seconds(self):
        """Tests formatting for durations under 120 seconds."""
        assert format_duration(30) == "30 seconds"
        assert format_duration(119) == "119 seconds"

    def test_format_duration_minutes(self):
        """Tests formatting for durations that are best expressed in minutes."""
        assert format_duration(300) == "300 seconds (~5.0 minutes)"
        assert format_duration(3600) == "3600 seconds (~60.0 minutes)"

    def test_format_duration_hours(self):
        """Tests formatting for durations that are best expressed in hours."""
        assert format_duration(7200) == "7200 seconds (~2.0 hours)"

# We can also have standalone test functions
def test_format_time_ago_just_now():
    """Tests the 'just now' case for format_time_ago."""
    from datetime import datetime, timezone, timedelta

    # A timestamp from 10 seconds ago
    ten_seconds_ago = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    assert format_time_ago(ten_seconds_ago) == "just now"

# --- NEW TEST CLASS FOR AN ASYNC FUNCTION ---

@pytest.mark.asyncio
class TestGetMediaInfo:
    
    async def test_get_media_info_success(self, mocker):
        """
        Tests get_media_info for a successful request where the media is found.
        """
        # 1. Arrange: Create a fake response object for a successful request
        mock_response = mocker.AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "image/png"}
        # The 'url' attribute of the response object holds the final URL after redirects
        mock_response.url = "https://final.url/image.png"
        
        # The response object is used in an 'async with' block, so we mock that behavior
        mock_response.__aenter__.return_value = mock_response

        # 2. Arrange: Create a fake session and make its 'head' method return our fake response
        mock_session = mocker.AsyncMock(spec=ClientSession)
        mock_session.head.return_value = mock_response
        
        # 3. Act: Call our real function, passing in the fake session
        content_type, final_url = await get_media_info("http://test.url/image.png", mock_session)

        # 4. Assert: Check that our function returned the correct data from the mock response
        assert content_type == "image/png"
        assert final_url == "https://final.url/image.png"

    async def test_get_media_info_not_found(self, mocker):
        """
        Tests get_media_info for a failed request (e.g., 404 Not Found).
        """
        # 1. Arrange: Create a fake response for a 404 error
        mock_response = mocker.AsyncMock()
        mock_response.status = 404
        mock_response.url = "http://test.url/notfound.png"
        mock_response.__aenter__.return_value = mock_response

        # 2. Arrange: Configure the fake session
        mock_session = mocker.AsyncMock(spec=ClientSession)
        mock_session.head.return_value = mock_response

        # 3. Act: Call our real function
        content_type, final_url = await get_media_info("http://test.url/notfound.png", mock_session)
        
        # 4. Assert: Check that our function correctly handled the error
        assert content_type is None
        assert final_url == "http://test.url/notfound.png"