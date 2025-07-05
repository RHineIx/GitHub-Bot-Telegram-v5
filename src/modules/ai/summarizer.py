# src/modules/ai/summarizer.py

import json
import logging
import textwrap
from typing import List, Optional

import google.generativeai as genai
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
from pydantic import BaseModel, Field, ValidationError

from src.core.config import Settings

logger = logging.getLogger(__name__)


class SelectedMedia(BaseModel):
    url: str = Field(..., description="The URL of the selected media file.")


class MediaSelectionResponse(BaseModel):
    selected_media: List[SelectedMedia]


class AISummarizer:
    """Encapsulates all interactions with the Google Gemini AI model."""

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

    async def summarize_readme(self, readme_content: str) -> Optional[str]:
        if not readme_content or len(readme_content) < 50:
            logger.debug("README content is too short to summarize.")
            return None

        prompt = textwrap.dedent(
            f"""
            You are an expert technical writer, skilled at creating clear and concise software summaries. Your task is to analyze the following README and generate a summary for a preview card in a messaging app.

            **CRITICAL INSTRUCTIONS:**
            1.  **Extract Core Information:** Identify and extract the most critical information. Structure your summary with the project's main purpose (a one-sentence pitch) followed by 2-4 key features.
            2.  **Exclusions:** You MUST ignore sections about installation, configuration, donation, licensing, and usage examples. Focus only on what the project IS and what it DOES.
            3.  **Neutral, Technical Tone:** Preserve the original text's tone. Do not add marketing fluff or enthusiastic language.
            4.  **Formatting:** The entire output MUST be plain text. Use line breaks for readability. Do NOT use any Markdown or HTML.
            5.  **Strict Character Limit:** The final output MUST NOT EXCEED 680 characters.

            **Original README content to process:**
            ---
            {readme_content[:12000]}
            ---
            """
        )

        try:
            logger.info("Sending README to Gemini for summarization...")
            response = await self.model.generate_content_async(prompt)
            summary = response.text.strip().strip('"')
            logger.info("Successfully received summary from Gemini.")
            return summary if summary else None
        except Exception as e:
            logger.error(
                f"An error occurred during README summarization with Gemini: {e}"
            )
            return None

    async def select_preview_media(
        self, readme_content: str, media_urls: List[str]
    ) -> List[str]:
        if not media_urls:
            return []

        formatted_url_list = "\n".join(f"- {url}" for url in media_urls)

        prompt = textwrap.dedent(
            f"""
            You are an expert UI/UX analyst. Your task is to select the 1 to 3 best media files from the provided list that visually represent a software project, based on its README file.

            **ANALYSIS PRIORITIES:**
            1.  **High-Value Sections:** Prioritize media found under headings like "Preview", "Demo", "Screenshots", "Showcase", "Features", or "How it works".
            2.  **Media Type Preference:** Prefer videos (.mp4, .webm) over images (.png, .jpg) as they are more descriptive.
            3.  **Content is Key:** Choose media that clearly demonstrates the project's purpose or user interface.
            4.  **Exclusions:** IGNORE media from sections like "Sponsors", "Contributors", "License", or "Badges".
            5.  **Hosting Services:** AVOID links from file-hosting websites like MediaFire, Dropbox, Google Drive, Mega.nz, etc. Prioritize direct links to media files (e.g., raw GitHub content, Imgur) or embedded videos from platforms like YouTube.

            **CRITICAL OUTPUT FORMAT:**
            - Your entire response MUST be a single, valid JSON object.
            - The JSON object must match this schema: `{{"selected_media": [{{"url": "string"}}]}}`
            - Do not add any explanation, preamble, or markdown. Only the raw JSON object.

            **README Content to Analyze:**
            ---
            {readme_content[:12000]}
            ---

            **Available Media URLs:**
            ---
            {formatted_url_list}
            ---
            """
        )

        try:
            logger.info("Asking Gemini to select the best preview media...")
            generation_config = GenerationConfig(response_mime_type="application/json")
            response = await self.model.generate_content_async(
                prompt, generation_config=generation_config
            )
            validated_response = MediaSelectionResponse.model_validate(
                json.loads(response.text)
            )
            selected_urls = [item.url for item in validated_response.selected_media]
            logger.info(f"Gemini selected {len(selected_urls)} valid media URLs.")
            return selected_urls[:3]
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(
                f"Failed to decode or validate Gemini's JSON response for media selection: {e}"
            )
            return []
        except Exception as e:
            logger.error(f"An error occurred during media selection with Gemini: {e}")
            return []
