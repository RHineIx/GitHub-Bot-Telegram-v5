# src/utils.py

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import aiohttp
from bs4 import BeautifulSoup

from src.modules.github.models import Repository

logger = logging.getLogger(__name__)


def format_duration(seconds: int) -> str:
    """Formats a duration in seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds} seconds"
    
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} minutes and {sec} seconds"
        
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} hours and {mins} minutes"
        
    days, hrs = divmod(hours, 24)
    return f"{days} days and {hrs} hours"


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


def format_release_date(dt: datetime) -> str:
    """
    Formats a datetime object into a detailed string with absolute and relative time.
    Example: 02.07.25 at 02:20 PM ( 2 days ago )
    """
    if not isinstance(dt, datetime):
        return "N/A"
    
    absolute_str = dt.strftime("%d.%m.%y at %I:%M %p")
    relative_str = format_time_ago(dt)
    
    return f"{absolute_str} ({relative_str})"


def extract_media_from_readme(markdown_text: str, repo: Repository) -> List[str]:
    """
    Extracts media URLs from README markdown, intelligently converting GitHub blob
    URLs to raw content URLs suitable for embedding.
    """
    if not markdown_text:
        return []
    
    # Regex patterns to find image/video URLs in markdown or HTML tags
    patterns = [
        r"\!\[.*?\]\(([^)\s]+)\)",        # Markdown images: ![alt](url)
        r'<img.*?src=[\'"]([^\'"]+)[\'"]',   # HTML images: <img src="...">
        r'<video.*?src=[\'"]([^\'"]+)[\'"]', # HTML videos: <video src="...">
    ]
    
    found_urls = []
    for pattern in patterns:
        found_urls.extend(re.findall(pattern, markdown_text))

    absolute_urls = []
    for url in set(found_urls): # Use set to process unique URLs only
        # Clean URL by removing fragments
        url = url.split("#")[0]

        # --- REFACTORED LOGIC ---
        # If the URL is a GitHub blob link, convert it to a raw link
        if "github.com" in url and "/blob/" in url:
            # Replace "github.com" with "raw.githubusercontent.com" and remove "/blob"
            raw_url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
            absolute_urls.append(raw_url)
        # If the URL is already a raw link, use it directly
        elif "raw.githubusercontent.com" in url:
            absolute_urls.append(url)
        # If the URL is a relative path, construct the full raw URL
        elif not url.startswith("http"):
            clean_path = url.lstrip("./").lstrip("/")
            # Construct the standard raw content URL
            raw_url = f"https://raw.githubusercontent.com/{repo.full_name}/{repo.default_branch_ref.name}/{clean_path}"
            absolute_urls.append(raw_url)
        # For other absolute URLs (e.g., imgur, etc.), add them directly
        else:
            absolute_urls.append(url)
            
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
    Cleans and formats GitHub release notes from Markdown to a Telegram-friendly format.
    This version uses BeautifulSoup as a final sanitizer to guarantee well-formed HTML.
    """
    if not text:
        return ""
    
    allowed_tags = ['b', 'i', 'a', 's', 'code', 'pre']
    pattern = r'</?(?!(' + '|'.join(allowed_tags) + r'))\w+[^>]*>'
    text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        line = line.strip()
        if re.fullmatch(r'\s*[-*_]{3,}\s*', line):
            continue
        if not line:
            cleaned_lines.append("")
            continue
        
        list_marker = ""
        match = re.match(r'^\s*([\-\*]|\d+\.)\s+', line)
        if match:
            list_marker = "• "
            line = line[match.end():]

        line = re.sub(r'^\s*#{1,6}\s*(.+?)\s*#*$', r'<b>\1</b>', line)
        line = re.sub(r'\*{2}(.+?)\*{2}', r'<b>\1</b>', line)
        line = re.sub(r'`([^`]+)`', r'<code>\1</code>', line)
        line = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', line)
        line = re.sub(r'\[![^\]]+\]\s*', '', line)
        line = re.sub(r'^\s*>\s?', '', line)

        match = re.search(r'https://github\.com/.+/(issues|pull)/(\d+)', line)
        if match:
            number = match.group(2)
            url = match.group(0)
            if url not in line[:line.find(url)]:
                line = re.sub(re.escape(url), f'<a href="{url}">#{number}</a>', line)

        if 'full changelog' in line.lower():
            line = re.sub(
                r'(https://github\.com/\S+/compare/\S+)',
                r'<a href="\1">View Full Changelog</a>',
                line,
                flags=re.IGNORECASE,
            )
            line = f"📄 <b>{line}</b>"
            
        cleaned_lines.append(f"{list_marker}{line}".strip())

    formatted = '\n'.join(cleaned_lines)
    formatted = re.sub(r'\n{3,}', '\n\n', formatted).strip()

    try:
        soup = BeautifulSoup(formatted, 'html.parser')
        clean_html = soup.decode_contents()
        return "\n".join(line.strip() for line in clean_html.splitlines()).strip()
    except Exception as e:
        logger.error(f"BeautifulSoup failed to parse cleaned notes, falling back. Error: {e}")
        return re.sub(r'<[^>]*>', '', text)


# Using a set is more idiomatic and slightly more performant for `in` checks.
EXCLUDED_KEYWORDS = {
    "badge", "sponsor", "donate", "logo", "gif", ".svg", "extension",
    "contributor", "shields.io", "badgen.net", "vercel.svg",
    "netlify.com/img/deploy", "app.codacy.com", "lgtm.com",
}

def is_url_excluded(url: str) -> bool:
    """
    Checks if a URL should be excluded based on a predefined set of keywords.
    Returns True if the URL contains any excluded keyword, otherwise False.
    """
    return any(kw in url.lower() for kw in EXCLUDED_KEYWORDS)