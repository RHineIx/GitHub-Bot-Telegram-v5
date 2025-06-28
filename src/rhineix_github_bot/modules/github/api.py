# src/rhineix_github_bot/modules/github/api.py

import logging
from typing import Any, Dict, List, Optional
import base64

import aiohttp
from pydantic import ValidationError

from rhineix_github_bot.core.config import Settings
from rhineix_github_bot.core.database import DatabaseManager
from .models import NotificationRepoData, StarredEvent, RateLimitData

logger = logging.getLogger(__name__)

# These queries are used to fetch the viewer's login and rate limit status.
VIEWER_LOGIN_QUERY = "query { viewer { login } }"
RATE_LIMIT_QUERY = "query { rateLimit { limit cost remaining resetAt } }"


# Define our GraphQL query as a constant for clarity and reuse
GET_REPO_DATA_FOR_NOTIFICATION_QUERY = """
query GetRepositoryNotificationData($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    description
    stargazerCount
    forkCount
    url
    pushedAt
    defaultBranchRef {
      name
    }
    owner {
      login
      avatarUrl
    }
    latestRelease: releases(first: 1, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        tagName
        url
      }
    }
    languages(first: 3, orderBy: {field: SIZE, direction: DESC}) {
      nodes {
        name
      }
    }
  }
}
"""

class GitHubAPIError(Exception):
    def __init__(self, status_code: int, message: str, errors: Optional[List] = None):
        self.status_code = status_code
        self.message = message
        self.errors = errors
        super().__init__(f"GitHub API Error {status_code}: {message}")


class GitHubAPI:
    """A GraphQL-first wrapper for the GitHub API."""

    def __init__(self, db_manager: DatabaseManager, settings: Settings):
        self.db_manager = db_manager
        self.settings = settings
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.settings.request_timeout))

    async def close(self):
        if self.session and not self.session.closed: await self.session.close()

    async def _get_headers(self) -> Dict[str, str]:
        headers = { "Accept": "application/json", "User-Agent": "Rhineix-GitHub-Bot/3.0-GraphQL" }
        token = await self.db_manager.get_token()
        if token: headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _execute_graphql_query(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a POST request to the GitHub GraphQL API."""
        headers = await self._get_headers()
        if "Authorization" not in headers: raise GitHubAPIError(401, "GitHub token not found.")
            
        payload = {"query": query, "variables": variables}

        async with self.session.post(self.settings.github_graphql_api, headers=headers, json=payload) as response:
            if 200 <= response.status < 300:
                json_response = await response.json()
                if "errors" in json_response:
                    raise GitHubAPIError(response.status, "GraphQL query returned errors.", errors=json_response["errors"])
                return json_response.get("data", {})
            
            raise GitHubAPIError(response.status, await response.text())

    # --- Public Methods ---

    # --- NEW: A dedicated method to validate a token ---
    async def get_viewer_login(self) -> Optional[str]:
        """
        Fetches the viewer's login to validate the current token.
        Returns the login name on success, None on failure.
        """
        try:
            data = await self._execute_graphql_query(VIEWER_LOGIN_QUERY, None)
            return data.get("viewer", {}).get("login")
        except GitHubAPIError:
            return None

    async def get_repository_data_for_notification(self, owner: str, repo: str) -> Optional[NotificationRepoData]:
        """Fetches all data needed for a repo notification in a single GraphQL call."""
        try:
            variables = {"owner": owner, "name": repo}
            data = await self._execute_graphql_query(GET_REPO_DATA_FOR_NOTIFICATION_QUERY, variables)
            return NotificationRepoData.model_validate(data) if data else None
        except (ValidationError, GitHubAPIError) as e:
            logger.error(f"Failed to get/validate GraphQL repo data for {owner}/{repo}: {e}")
            return None
        
    async def get_readme(self, owner: str, repo: str) -> Optional[str]:
        """
        Fetches and decodes the README for a repository using the intelligent v3 REST endpoint.
        """
        url = f"{self.settings.github_api_base}/repos/{owner}/{repo}/readme"
        headers = await self._get_headers()
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and "content" in data:
                        # Decode the base64 encoded content
                        return base64.b64decode(data["content"]).decode("utf-8")
                # Return None if not found (404) or on other errors
                return None
        except Exception as e:
            logger.error(f"Failed to fetch README for {owner}/{repo} via REST: {e}")
            return None


    async def get_readme_content(self, owner: str, repo: str, branch: str) -> Optional[str]:
        """Fetches and decodes the README using a direct raw content URL."""
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        try:
            async with self.session.get(url) as response:
                if response.status == 200: return await response.text()
        except Exception as e:
            logger.error(f"Failed to fetch README content for {owner}/{repo}: {e}")
        return None

    async def get_authenticated_user_starred_events(self) -> Optional[List[StarredEvent]]:
        """Gets the most recent starred events (still using REST for now)."""
        url = f"{self.settings.github_api_base}/user/starred?sort=created&direction=desc&per_page=30"
        headers = await self._get_headers()
        headers["Accept"] = "application/vnd.github.star+json"
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return [StarredEvent.model_validate(event) for event in data]
            return []
        except (ValidationError, aiohttp.ClientError) as e:
            logger.error(f"Failed to get/validate starred events via REST: {e}")
            return None
        
    async def get_rate_limit(self) -> Optional[RateLimitData]:
        """Fetches the current rate limit status using the GraphQL API."""
        try:
            data = await self._execute_graphql_query(RATE_LIMIT_QUERY, None)
            return RateLimitData.model_validate(data) if data else None
        except (ValidationError, GitHubAPIError) as e:
            logger.error(f"Failed to get/validate GraphQL rate limit: {e}")
            return None