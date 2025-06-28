# src/rhineix_github_bot/modules/telegram/filters.py
from aiogram.filters import Filter
from aiogram.types import Message
from rhineix_github_bot.core.config import Settings

class IsOwnerFilter(Filter):
    async def __call__(self, message: Message, settings: Settings) -> bool:
        return message.from_user.id == settings.owner_user_id