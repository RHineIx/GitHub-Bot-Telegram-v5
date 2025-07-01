# src/modules/github/api.py

import logging
from typing import Any, Dict, List, Optional
import base64

import aiohttp
from pydantic import ValidationError
from bs4 import BeautifulSoup

from src.core.config import Settings
from src.core.database import DatabaseManager
from .models import NotificationRepoData, StarredEvent, RateLimitData, ViewerListsData, NodeData

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
    # --- The languages query is now more detailed ---
    languages(first: 3, orderBy: {field: SIZE, direction: DESC}) {
      totalSize
      edges {
        size
        node {
          name
        }
      }
    }
  }
}
"""

# Query to get the viewer's repository lists AND THEIR IDs
GET_USER_REPOSITORY_LISTS_QUERY = """
query GetUserRepositoryListsWithID {
  viewer {
    lists(first: 20) {
      edges {
        node {
          id # <-- The crucial addition
          name
          slug
        }
      }
    }
  }
}
"""

# Query to get repositories from a specific List by its Node ID
GET_LIST_REPOS_BY_ID_QUERY = """
query GetListReposByID($listID: ID!) {
  node(id: $listID) {
    ... on UserList {
      repositories(first: 100) {
        nodes {
          nameWithOwner
        }
      }
    }
  }
}
"""

class GitHubAPIError(Exception):
    def __init__(self, db_manager: DatabaseManager, settings: Settings):
        self.db_manager = db_manager
        self.settings = settings
        
        # Define headers with a standard browser User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        }

        # Create the session with these headers
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.settings.request_timeout),
            headers=headers
        )


class GitHubAPI:
    """A GraphQL-first wrapper for the GitHub API."""

    def __init__(self, db_manager: DatabaseManager, settings: Settings):
        self.db_manager = db_manager
        self.settings = settings
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.settings.request_timeout)
        )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Rhineix-GitHub-Bot/3.0-GraphQL",
        }
        token = await self.db_manager.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _execute_graphql_query(
        self, query: str, variables: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Executes a POST request to the GitHub GraphQL API."""
        headers = await self._get_headers()
        if "Authorization" not in headers:
            raise GitHubAPIError(401, "GitHub token not found.")

        payload = {"query": query, "variables": variables}

        async with self.session.post(
            self.settings.github_graphql_api, headers=headers, json=payload
        ) as response:
            if 200 <= response.status < 300:
                json_response = await response.json()
                if "errors" in json_response:
                    # --- RE-ADD THIS TEMPORARY DEBUGGING LOG ---
                    logger.error(f"GitHub GraphQL API returned specific errors: {json_response['errors']}")
                    # ------------------------------------------
                    raise GitHubAPIError(
                        response.status,
                        "GraphQL query returned errors.",
                        errors=json_response["errors"],
                    )

                return json_response.get("data", {})

            raise GitHubAPIError(response.status, await response.text())
        
    async def get_repos_in_list_by_scraping(
        self, owner_login: str, list_slug: str
    ) -> Optional[List[str]]:
        """
        Gets repository names from a list by scraping its public HTML page.
        This is a fallback due to API limitations.
        """
        url = f"https://github.com/stars/{owner_login}/lists/{list_slug}"
        logger.info(f"Attempting to scrape repository list from: {url}")
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch list page {url}, status: {response.status}")
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                
                # This selector looks for links directly inside H3 tags, a common pattern for titles.
                repo_links = soup.select('h3 > a[href*="/"]')
                
                if not repo_links:
                    logger.warning(f"No repository links found on page {url} with the new selector. The page structure might have changed.")
                    # Add a debug log to see the HTML content if scraping fails
                    logger.debug(f"Page content received for scraping:\n{html}")
                    return []
                    
                repo_full_names = [link['href'].lstrip('/') for link in repo_links]
                logger.info(f"Successfully scraped {len(repo_full_names)} repos from list '{list_slug}'.")
                return repo_full_names

        except Exception as e:
            logger.error(f"An exception occurred during web scraping of list {list_slug}: {e}", exc_info=True)
            return None


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

    async def get_repository_data_for_notification(
        self, owner: str, repo: str
    ) -> Optional[NotificationRepoData]:
        """Fetches all data needed for a repo notification in a single GraphQL call."""
        try:
            variables = {"owner": owner, "name": repo}
            data = await self._execute_graphql_query(
                GET_REPO_DATA_FOR_NOTIFICATION_QUERY, variables
            )
            return NotificationRepoData.model_validate(data) if data else None
        except (ValidationError, GitHubAPIError) as e:
            logger.error(
                f"Failed to get/validate GraphQL repo data for {owner}/{repo}: {e}"
            )
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

    async def get_readme_content(
        self, owner: str, repo: str, branch: str
    ) -> Optional[str]:
        """Fetches and decodes the README using a direct raw content URL."""
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.text()
        except Exception as e:
            logger.error(f"Failed to fetch README content for {owner}/{repo}: {e}")
        return None

    async def get_authenticated_user_starred_events(
        self,
    ) -> Optional[List[StarredEvent]]:
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


    async def get_user_repository_lists(self) -> Optional[ViewerListsData]:
            """Fetches the viewer's created repository lists."""
            try:
                data = await self._execute_graphql_query(GET_USER_REPOSITORY_LISTS_QUERY, {})
                # The structure is nested under 'viewer'
                return ViewerListsData.model_validate(data.get("viewer", {})) if data else None
            except (ValidationError, GitHubAPIError) as e:
                logger.error(f"Failed to get/validate GraphQL user repo lists: {e}")
                return None