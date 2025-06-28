# src/rhineix_github_bot/modules/github/formatter.py

import logging
from typing import Optional

from rhineix_github_bot.utils import format_time_ago
from .models import Repository

logger = logging.getLogger(__name__)


class RepoFormatter:
    """Formats GraphQL repository data for display in Telegram messages."""

    @staticmethod
    def _format_number(num: int) -> str:
        """Abbreviates large numbers (e.g., 12345 -> 12.3K)."""
        if num >= 1_000_000:
            return f"{num / 1_000_000:.1f}M"
        if num >= 1_000:
            return f"{num / 1_000:.1f}K"
        return str(num)

    @staticmethod
    def format_repository_preview(
        repo: Repository,
        ai_summary: Optional[str] = None,
    ) -> str:
        """Constructs the main HTML message for a repository preview from GraphQL data."""
        description = ai_summary or repo.description or "No description available."
        
        stars = RepoFormatter._format_number(repo.stargazer_count)
        forks = RepoFormatter._format_number(repo.fork_count)
        
        last_updated_str = f'{repo.pushed_at.strftime("%Y-%m-%d")} ({format_time_ago(repo.pushed_at.isoformat())})'

        # Safely access the latest release from the nested model
        release_info = "No official releases"
        if repo.latest_release and repo.latest_release.nodes:
            release_node = repo.latest_release.nodes[0]
            release_info = f"<a href='{release_node.url}'>{release_node.tag_name}</a>"

        # Safely access languages from the nested model
        languages_text = "Not specified"
        if repo.languages and repo.languages.nodes:
            top_languages = [lang.name for lang in repo.languages.nodes]
            languages_text = " ".join([f"#{lang.replace('-', '_')}" for lang in top_languages])

        return (
            f"📦 <a href='{repo.url}'>{repo.name_with_owner}</a>\n\n"
            f"<blockquote expandable>📝 {description}</blockquote>\n\n"
            f"⭐ <b>Stars:</b> {stars} | 🍴 <b>Forks:</b> {forks}\n\n"
            f"🚀 <b>Latest Release:</b> {release_info}\n"
            f"⏳ <b>Last updated:</b> {last_updated_str}\n"
            f"💻 <b>Langs:</b> {languages_text}\n\n"
            f"<a href='{repo.url}'>🔗 View on GitHub</a>"
        ).strip()