# src/utils.py

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import aiohttp
from bs4 import BeautifulSoup

from src.modules.github.models import Repository

logger = logging.getLogger(__name__)


def format_duration(seconds: int) -> str:
    if seconds < 120:
        return f"{seconds} seconds"
    minutes = seconds / 60
    if minutes < 120:
        return f"{seconds} seconds (~{minutes:.1f} minutes)"
    hours = minutes / 60
    return f"{seconds} seconds (~{hours:.1f} hours)"


def format_time_ago(timestamp_str: str) -> str:
    try:
        if isinstance(timestamp_str, datetime):
            date_obj = timestamp_str
        else:
            date_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "N/A"
    now = datetime.now(timezone.utc)
    is_future = date_obj > now
    delta = date_obj - now if is_future else now - date_obj
    seconds = int(delta.total_seconds())
    days = delta.days
    template = "in {}" if is_future else "{} ago"
    if seconds < 60:
        return "just now" if not is_future else "in a moment"
    elif seconds < 3600:
        minutes = seconds // 60
        unit = f"{minutes} minute" + ("s" if minutes > 1 else "")
        return template.format(unit)
    elif seconds < 86400:
        hours = seconds // 3600
        unit = f"{hours} hour" + ("s" if hours > 1 else "")
        return template.format(unit)
    elif days < 365:
        unit = f"{days} day" + ("s" if days > 1 else "")
        return template.format(unit)
    else:
        years = days // 365
        unit = f"{years} year" + ("s" if years > 1 else "")
        return template.format(unit)


def extract_media_from_readme(markdown_text: str, repo: Repository) -> List[str]:
    if not markdown_text:
        return []
    patterns = [
        r"\!\[.*?\]\(([^)\s]+)\)",
        r'<img.*?src=[\'"]([^\'"]+)[\'"]',
        r'<video.*?src=[\'"]([^\'"]+)[\'"]',
    ]
    urls = []
    for pattern in patterns:
        urls.extend(re.findall(pattern, markdown_text))
    absolute_urls = []
    for url in set(urls):
        url = url.split("#")[0]
        if url.startswith("http"):
            absolute_urls.append(url)
        else:
            clean_path = url
            if clean_path.startswith("./"):
                clean_path = clean_path[2:]
            elif clean_path.startswith("/"):
                clean_path = clean_path[1:]
            absolute_urls.append(
                f"https://raw.githubusercontent.com/{repo.full_name}/{repo.default_branch_ref.name}/{clean_path}"
            )
    return absolute_urls


async def get_media_info(
    url: str, session: aiohttp.ClientSession
) -> Optional[Tuple[str, str]]:
    try:
        async with session.head(url, timeout=15, allow_redirects=True) as response:
            final_url = str(response.url)
            if response.status == 200:
                content_type = response.headers.get("Content-Type", "").lower()
                return content_type, final_url
            return None, final_url
    except Exception as e:
        logger.debug(f"Could not get media info for {url}: {e}")
        return None, url


async def scrape_social_preview_image(
    url: str, session: aiohttp.ClientSession
) -> Optional[str]:
    """Scrapes a URL for its 'og:image' social media preview image."""
    try:
        async with session.get(url, timeout=15) as response:
            if response.status != 200:
                return None
            soup = BeautifulSoup(await response.text(), "html.parser")
            og_image_tag = soup.find("meta", property="og:image")
            if og_image_tag and og_image_tag.get("content"):
                return og_image_tag.get("content")
    except Exception as e:
        logger.error(f"Exception while scraping {url} for social preview: {e}")
    return None

def clean_release_notes(text: str) -> str:
    """
    Cleans and formats GitHub release notes from Markdown to a Telegram-friendly HTML format.
    """
    if not text:
        return ""

    # to handle nested formatting correctly.
    replacements = [
        # --- Block-level elements first ---
        # Remove GitHub-specific alert syntax like [!NOTE]
        (r'\[![^\]]+\]\s*', ''),
        # Convert Markdown headings (e.g., ### Title) to bold
        (r'^\s*#{1,6}\s*(.+?)\s*#*\s*$', r'<b>\1</b>'),
        # Convert Markdown list items (* or -) to bullet points (•)
        (r'^\s*[\*\-]\s+', '• '),
        # Remove blockquote markers (>)
        (r'^\s*>\s?', ''),
        # Remove horizontal rules (---, ***, etc.)
        (r'^\s*[-*_]{3,}\s*$', ''),

        # --- Inline elements second ---
        # Convert Markdown links [text](url) to HTML links
        (r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>'),
        
        # --- Text formatting (order is critical) ---
        # Bold and Italic (e.g., ***text***)
        (r'\*{3}(.+?)\*{3}', r'<b><i>\1</i></b>'),
        (r'_{3}(.+?)_{3}', r'<b><i>\1</i></b>'),
        
        # Bold (e.g., **text**)
        (r'\*{2}(.+?)\*{2}', r'<b>\1</b>'),
        (r'_{2}(.+?)_{2}', r'<b>\1</b>'),
        
        # Italic (e.g., *text*)
        (r'\*(.+?)\*', r'<i>\1</i>'),
        (r'_(.+?)_', r'<i>\1</i>'),
        
        # Strikethrough (e.g., ~~text~~)
        (r'~~(.+?)~~', r'<s>\1</s>'),
    ]

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)

    # Clean up excessive newlines, but keep double newlines for paragraphs
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()