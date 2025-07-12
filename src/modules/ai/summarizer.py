# src/modules/ai/summarizer.py

import json
import logging
import textwrap
import asyncio
import random
from typing import List, Optional, Callable, Any

import google.generativeai as genai
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
from google.api_core.exceptions import ResourceExhausted
from pydantic import BaseModel, Field, ValidationError

from src.core.config import Settings
from src.utils import EXCLUDED_KEYWORDS

logger = logging.getLogger(__name__)


class SelectedMedia(BaseModel):
    url: str = Field(..., description="The URL of the selected media file.")


class MediaSelectionResponse(BaseModel):
    selected_media: List[SelectedMedia]


class AISummarizer:
    """
    Encapsulates all interactions with the Google Gemini AI model,
    with built-in resilience for handling API rate limits.
    """

    def __init__(self, settings: Settings):
        if not settings.gemini_api_key:
            raise ValueError("Gemini API key is required to initialize AISummarizer.")
        genai.configure(api_key=settings.gemini_api_key)
        self.model_name = settings.gemini_model_name
        logger.info(f"Initializing Gemini with model: {self.model_name}")
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        self.model = genai.GenerativeModel(
            self.model_name, safety_settings=safety_settings
        )
        self.max_retries = 5
        self.base_delay = 2

    async def _execute_with_retry(self, api_call: Callable[..., Any], *args, **kwargs) -> Optional[Any]:
        """
        Executes a Gemini API call with exponential backoff for rate limit errors.
        """
        attempt = 0
        while attempt < self.max_retries:
            try:
                return await api_call(*args, **kwargs)
            except ResourceExhausted as e:
                attempt += 1
                if attempt >= self.max_retries:
                    logger.error(f"API call failed after {self.max_retries} attempts. Giving up. Error: {e}")
                    return None
                
                delay = (self.base_delay ** attempt) + (random.uniform(0, 1))
                logger.warning(
                    f"Rate limit exceeded. Retrying in {delay:.2f} seconds... (Attempt {attempt}/{self.max_retries})"
                )
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"An unexpected error occurred during API call {api_call.__name__}: {e}")
                return None
        return None

    async def summarize_readme(self, readme_content: str) -> Optional[str]:
        if not readme_content or len(readme_content) < 150:
            logger.debug("README content is too short to summarize.")
            return None

        prompt = textwrap.dedent(
            f"""
            You are an expert technical writer, skilled at creating clear and concise software summaries.
            Analyze the following README and generate a summary for a preview card.
            **Instructions:**
            1.  **Core Purpose:** Start with a one-sentence pitch that explains the project's main goal.
            2.  **Key Features:** Follow with 2-4 key features in plain text.
            3.  **Exclusions:** Ignore installation, configuration, donation, and licensing sections.
            4.  **Tone:** Maintain a neutral, technical tone. No marketing language.
            5.  **Format:** The entire output must be plain text. Do NOT use Markdown or HTML.
            6.  **Character Limit:** The final summary must NOT EXCEED 680 characters.
            
            **README to process:**
            ---
            {readme_content[:12000]}
            ---
            """
        )

        logger.info("Sending README to Gemini for summarization...")
        response = await self._execute_with_retry(self.model.generate_content_async, prompt)
        
        if response:
            summary = response.text.strip().strip('"')
            logger.info("Successfully received summary from Gemini.")
            return summary if summary else None
        return None

    async def select_preview_media(self, readme_content: str, media_urls: List[str]) -> List[str]:
        if not media_urls:
            return []

        formatted_url_list = "\n".join(f"- {url}" for url in media_urls)
        excluded_keywords_str = ", ".join(sorted(list(EXCLUDED_KEYWORDS)))

        prompt = textwrap.dedent(
            f"""
            You are a UI/UX analyst. Select the 1 to 3 best media files from the list that visually represent the software project, based on its README.
            **Priorities:**
            1.  **High-Value Sections:** Prioritize media from "Preview", "Demo", "Screenshots", or "Features".
            2.  **Media Type:** Prefer videos (.mp4, .webm) over images.
            3.  **Exclusions:** IGNORE media from "Sponsors" or "Badges". Do NOT select any URL containing these keywords: {excluded_keywords_str}.
            
            **CRITICAL OUTPUT FORMAT:**
            - Your entire response MUST be a single, valid JSON object.
            - Schema: `{{"selected_media": [{{"url": "string"}}]}}`
            - Do not add any explanation or markdown. Only the raw JSON.

            **README Content:**
            ---
            {readme_content[:10000]}
            ---

            **Available Media URLs:**
            ---
            {formatted_url_list}
            ---
            """
        )

        logger.info("Asking Gemini to select the best preview media...")
        generation_config = GenerationConfig(response_mime_type="application/json")
        response = await self._execute_with_retry(
            self.model.generate_content_async, prompt, generation_config=generation_config
        )

        if not response:
            return []

        try:
            validated_response = MediaSelectionResponse.model_validate(json.loads(response.text))
            selected_urls = [item.url for item in validated_response.selected_media]
            logger.info(f"Gemini selected {len(selected_urls)} valid media URLs.")
            return selected_urls[:3]
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"Failed to decode or validate Gemini's JSON response: {e}")
            return []