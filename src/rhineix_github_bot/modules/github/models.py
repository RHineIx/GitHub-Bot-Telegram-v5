# src/rhineix_github_bot/modules/github/models.py

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# --- Models for Notification Data (from GraphQL) ---

class LanguageNode(BaseModel):
    name: str

class Languages(BaseModel):
    nodes: List[LanguageNode]

class ReleaseNode(BaseModel):
    tag_name: str = Field(..., alias="tagName")
    url: str

class LatestRelease(BaseModel):
    nodes: List[ReleaseNode]

class Owner(BaseModel):
    login: str
    avatar_url: str = Field(..., alias="avatarUrl")

class DefaultBranchRef(BaseModel):
    name: str

class Repository(BaseModel):
    name_with_owner: str = Field(..., alias="nameWithOwner")
    full_name: str = Field(..., alias="nameWithOwner") # For compatibility
    description: Optional[str] = None
    stargazer_count: int = Field(..., alias="stargazerCount")
    fork_count: int = Field(..., alias="forkCount")
    url: str
    pushed_at: datetime = Field(..., alias="pushedAt")
    default_branch_ref: DefaultBranchRef = Field(..., alias="defaultBranchRef")
    owner: Owner
    latest_release: Optional[LatestRelease] = Field(None, alias="latestRelease")
    languages: Optional[Languages] = None


class NotificationRepoData(BaseModel):
    """The root model for the repository data we fetch for a notification."""
    repository: Optional[Repository] = None

# --- Models for Starred Events (from REST API - kept for now) ---

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
