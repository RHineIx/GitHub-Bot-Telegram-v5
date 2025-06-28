# src/rhineix_github_bot/utils.py

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import aiohttp
from bs4 import BeautifulSoup

from rhineix_github_bot.modules.github.models import Repository

logger = logging.getLogger(__name__)


def format_duration(seconds: int) -> str:
    if seconds < 120: return f"{seconds} seconds"
    minutes = seconds / 60
    if minutes < 120: return f"{seconds} seconds (~{minutes:.1f} minutes)"
    hours = minutes / 60
    return f"{seconds} seconds (~{hours:.1f} hours)"

def format_time_ago(timestamp_str: str) -> str:
    try:
        if isinstance(timestamp_str, datetime): date_obj = timestamp_str
        else: date_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, TypeError): return "N/A"
    now = datetime.now(timezone.utc)
    is_future = date_obj > now
    delta = date_obj - now if is_future else now - date_obj
    seconds = int(delta.total_seconds())
    days = delta.days
    template = "in {}" if is_future else "{} ago"
    if seconds < 60: return "just now" if not is_future else "in a moment"
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
    if not markdown_text: return []
    patterns = [ r'\!\[.*?\]\(([^)\s]+)\)', r'<img.*?src=[\'"]([^\'"]+)[\'"]', r'<video.*?src=[\'"]([^\'"]+)[\'"]' ]
    urls = []
    for pattern in patterns: urls.extend(re.findall(pattern, markdown_text))
    absolute_urls = []
    for url in set(urls):
        url = url.split('#')[0]
        if url.startswith("http"): absolute_urls.append(url)
        else:
            clean_path = url
            if clean_path.startswith('./'): clean_path = clean_path[2:]
            elif clean_path.startswith('/'): clean_path = clean_path[1:]
            absolute_urls.append( f"https://raw.githubusercontent.com/{repo.full_name}/{repo.default_branch_ref.name}/{clean_path}")
    return absolute_urls

async def get_media_info(url: str, session: aiohttp.ClientSession) -> Optional[Tuple[str, str]]:
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

async def scrape_social_preview_image(owner: str, repo: str, session: aiohttp.ClientSession) -> Optional[str]:
    repo_url = f"https://github.com/{owner}/{repo}"
    try:
        async with session.get(repo_url, timeout=15) as response:
            if response.status != 200: return None
            soup = BeautifulSoup(await response.text(), "html.parser")
            og_image_tag = soup.find("meta", property="og:image")
            if og_image_tag and og_image_tag.get("content"): return og_image_tag.get("content")
    except Exception as e:
        logger.error(f"Exception while scraping {repo_url} for social preview: {e}")
    return None