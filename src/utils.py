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


def format_release_date(dt: datetime) -> str:
    """
    Formats a datetime object into a detailed string with absolute and relative time.
    Example: 02.07.25 at 02:20 PM ( 2 days ago )
    """
    if not isinstance(dt, datetime):
        return "N/A"
    
    # Format the absolute date and time part: dd.mm.yy at H:M AM/PM
    absolute_str = dt.strftime("%d.%m.%y at %I:%M %p")
    
    # Get the relative time part using our existing function
    relative_str = format_time_ago(dt)
    
    # Combine them into the desired format
    return f"{absolute_str} ({relative_str})"


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
    Cleans and formats GitHub release notes from Markdown to a Telegram-friendly format.
    This version uses BeautifulSoup as a final sanitizer to guarantee well-formed HTML.
    """
    if not text:
        return ""
    
    # Strip unsupported HTML tags first
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

    # --- sanitization step using BeautifulSoup ---
    # This robustly fixes any malformed or improperly nested tags
    # that our regex substitutions might have accidentally created.
    try:
        # Parse the generated HTML fragment.
        soup = BeautifulSoup(formatted, 'html.parser')
        
        # Convert it back to a string. BeautifulSoup automatically corrects errors.
        # .decode_contents() gets the inner HTML without the <html><body> wrapper.
        clean_html = soup.decode_contents()
        
        # cleanup of any extra whitespace introduced by the parser.
        return "\n".join(line.strip() for line in clean_html.splitlines()).strip()
    except Exception as e:
        logger.error(f"BeautifulSoup failed to parse cleaned notes, falling back to plain text. Error: {e}")
        # If even BeautifulSoup fails, fall back to the safest possible text.
        return re.sub(r'<[^>]*>', '', text)