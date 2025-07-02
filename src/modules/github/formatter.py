# src/modules/github/formatter.py

import logging
from typing import Optional

from src.utils import format_time_ago, clean_release_notes, format_release_date
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
        if repo.languages and repo.languages.edges and repo.languages.total_size > 0:
            lang_texts = []
            for edge in repo.languages.edges:
                percentage = (edge.size / repo.languages.total_size) * 100
                lang_name = edge.node.name.replace('-', '_')
                lang_texts.append(f"#{lang_name} (<code>{percentage:.1f}%</code>)")
            languages_text = " ".join(lang_texts)

        return (
            f"📦 <a href='{repo.url}'>{repo.name_with_owner}</a>\n\n"
            f"<blockquote expandable>📝 {description}</blockquote>\n\n"
            f"⭐ <b>Stars:</b> {stars} | 🍴 <b>Forks:</b> {forks}\n\n"
            f"🚀 <b>Latest Release:</b> {release_info}\n"
            f"⏳ <b>Last updated:</b> {last_updated_str}\n"
            f"💻 <b>Langs:</b> {languages_text}\n\n"
            f"<a href='{repo.url}'>🔗 View on GitHub</a>"
        ).strip()

    @staticmethod
    def format_release_notification(repo: Repository) -> str:
        """Constructs the HTML message for a new release notification."""
        release_node = repo.latest_release.nodes[0]
        
        # Start building the base message
        message_parts = [
            f"🚀 <b>New Release: <a href='{repo.url}'>{repo.name_with_owner}</a></b>",
            f"└─ 🔖 <code>{release_node.tag_name}</code>"
        
        # Add the published date if it exists
        ]
        if release_node.published_at:
            published_str = format_release_date(release_node.published_at)
            message_parts.append(f"└─ 🗓️ Published: {published_str}")

        # Add the release notes if they exist, inside an expandable blockquote
        if release_node.description:
            # Clean the markdown from the description before displaying it
            notes = clean_release_notes(release_node.description)
            
            # Truncate to a reasonable length to avoid hitting Telegram limits
            if len(notes.encode('utf-8')) > 2000:
                notes = notes.encode('utf-8')[:1997].decode('utf-8', errors='ignore') + "..."
            
            message_parts.append(f"\n<blockquote expandable>📝 <b>Release Notes:</b>\n{notes}</blockquote>")

        repo_name_hashtag = repo.name_with_owner.split('/')[-1].replace('-', '_').capitalize()
        message_parts.append(f"\n#NewRelease - #Releases{repo_name_hashtag}")

        return "\n".join(message_parts)
