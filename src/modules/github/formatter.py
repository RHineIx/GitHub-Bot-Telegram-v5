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

        # Use AI summary if available, otherwise fall back to the repo's description.
        description = ai_summary or repo.description or "No description available."

        # This protects against both long repo descriptions and unexpectedly long AI summaries.
        if len(description) > 700:
            description = description[:697] + "..."
            

        stars = RepoFormatter._format_number(repo.stargazer_count)
        forks = RepoFormatter._format_number(repo.fork_count)
        issues_count = repo.issues.total_count if repo.issues else 0

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

        # Format license
        license_text = ""
        if repo.license_info:
            license_text = f"📜 <b>License:</b> {repo.license_info.name}\n"

        # Build the main part of the message
        message = (
            f"📦 <a href='{repo.url}'>{repo.name_with_owner}</a>\n\n"
            f"<blockquote expandable>📝 {description}</blockquote>\n\n"
            f"⭐ <b>Stars:</b> <code>{stars}</code> | 🍴 <b>Forks:</b> <code>{forks}</code> | 🪲 Open Issues: <code>{issues_count}</code>\n\n"
            f"{license_text}\n"
            f"🚀 <b>Latest Release:</b> {release_info}\n"
            f"⏳ <b>Last updated:</b> {last_updated_str}\n"
            f"💻 <b>Langs:</b> {languages_text}\n\n"
            f"<a href='{repo.url}'>🔗 View on GitHub</a>"
        )

        # --- Format and add topics at the end ---
        if repo.repository_topics and repo.repository_topics.nodes:
            # Replace hyphens with underscores and prepend a hashtag
            topic_hashtags = [
                f"#{t.topic.name.replace('-', '_')}" 
                for t in repo.repository_topics.nodes
            ]
            # Add a double newline for spacing, then join the topics
            message += "\n\n" + " ".join(topic_hashtags)

        return message.strip()

    @staticmethod
    def format_release_notification(repo: Repository) -> str:
        """Constructs the HTML message for a new release notification."""
        release_node = repo.latest_release.nodes[0]
        
        message_parts = [
            f"🚀 <b>New Release: <a href='{repo.url}'>{repo.name_with_owner}</a></b>",
            f"└─ 🔖 <code>{release_node.tag_name}</code>"
        ]

        if release_node.published_at:
            published_str = format_release_date(release_node.published_at)
            message_parts.append(f"└─ 🗓️ Published: {published_str}")

        # Add the release notes if they exist, with safe truncation
        if release_node.description:
            raw_notes = release_node.description
            
            # Truncate the raw text first to a length suitable for captions
            if len(raw_notes) > 1000:
                raw_notes = raw_notes[:897] + "..."
            
            # Now, clean and format the already-shortened text
            notes = clean_release_notes(raw_notes)
            
            message_parts.append(f"\n<blockquote expandable>📝 <b>Release Notes:</b>\n{notes}</blockquote>")

        repo_name_hashtag = repo.name_with_owner.split('/')[-1].replace('-', '_').replace('.', '').capitalize()
        message_parts.append(f"\n#NewRelease - #Releases{repo_name_hashtag}")

        return "\n".join(message_parts)