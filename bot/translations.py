import structlog
from typing import Optional
from bot.bot_texts import TextsStore

logger = structlog.getLogger(__name__)

def get_translation(key: str) -> str:
    """Получение перевода с поддержкой мультиязычности"""
    return TextsStore.get(key)
