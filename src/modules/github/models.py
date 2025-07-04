# src/modules/github/models.py

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# --- Models for Notification Data (from GraphQL) ---


class LanguageNode(BaseModel):
    name: str


class LanguageEdge(BaseModel):
    """Represents the connection between the repo and a language, holding size info."""
    size: int
    node: LanguageNode


class Languages(BaseModel):
    """Holds the list of language edges and the total size."""
    total_size: int = Field(..., alias="totalSize")
    edges: List[LanguageEdge]

class LicenseInfo(BaseModel):
    name: str


class Topic(BaseModel):
    name: str

class TopicNode(BaseModel):
    topic: Topic

class RepositoryTopics(BaseModel):
    nodes: List[TopicNode]


class ReleaseNode(BaseModel):
    id: str
    tag_name: str = Field(..., alias="tagName")
    url: str
    description: Optional[str] = None
    published_at: Optional[datetime] = Field(None, alias="publishedAt")


class LatestRelease(BaseModel):
    nodes: List[ReleaseNode]


class Owner(BaseModel):
    login: str
    avatar_url: str = Field(..., alias="avatarUrl")


class DefaultBranchRef(BaseModel):
    name: str


class Repository(BaseModel):
    name_with_owner: str = Field(..., alias="nameWithOwner")
    full_name: str = Field(..., alias="nameWithOwner")
    license_info: Optional[LicenseInfo] = Field(None, alias="licenseInfo")
    description: Optional[str] = None
    stargazer_count: int = Field(..., alias="stargazerCount")
    fork_count: int = Field(..., alias="forkCount")
    url: str
    pushed_at: datetime = Field(..., alias="pushedAt")
    default_branch_ref: DefaultBranchRef = Field(..., alias="defaultBranchRef")
    owner: Owner
    latest_release: Optional[LatestRelease] = Field(None, alias="latestRelease")
    languages: Optional[Languages] = None
    repository_topics: Optional[RepositoryTopics] = Field(None, alias="repositoryTopics")


class NotificationRepoData(BaseModel):
    """The root model for the repository data we fetch for a notification."""

    repository: Optional[Repository] = None


# --- Models for Starred Events (from REST API)

class StarredEventRepo(BaseModel):
    id: int
    full_name: str


class StarredEvent(BaseModel):
    """Pydantic model for a "starred" event from the REST API user feed."""

    starred_at: datetime
    repository: StarredEventRepo = Field(..., validation_alias="repo")


class RateLimit(BaseModel):
    """Pydantic model for the GraphQL rateLimit object."""

    limit: int
    cost: int
    remaining: int
    reset_at: datetime = Field(..., alias="resetAt")


class RateLimitData(BaseModel):
    """The root model for the rate limit query."""

    rate_limit: Optional[RateLimit] = Field(None, alias="rateLimit")


# --- Models for GitHub Repository Lists (GraphQL) ---

class RepositoryInList(BaseModel):
    """A simplified repository model for items within a list."""
    name_with_owner: str = Field(..., alias="nameWithOwner")


class RepositoriesInListConnection(BaseModel):
    nodes: List[RepositoryInList]


class RepositoryList(BaseModel):
    """Represents a single GitHub List."""
    name: str
    slug: str


class RepositoryListEdge(BaseModel):
    node: RepositoryList


class RepositoryListsConnection(BaseModel):
    edges: List[RepositoryListEdge]


class ViewerListsData(BaseModel):
    """The root model for the user's repository lists query."""
    lists: RepositoryListsConnection