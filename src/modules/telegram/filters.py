# src/modules/telegram/filters.py
from aiogram.filters import Filter
from aiogram.types import Message
from src.core.config import Settings


class IsOwnerFilter(Filter):
    async def __call__(self, message: Message, settings: Settings) -> bool:
        return message.from_user.id == settings.owner_user_id
