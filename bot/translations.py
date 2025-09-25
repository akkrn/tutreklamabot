import structlog
from typing import Optional
from bot.bot_texts import TextsStore
from bot.middlewares import current_user_language

logger = structlog.getLogger(__name__)

def get_translation(key: str, language_code: Optional[str] = None) -> str:
    """Получение перевода с поддержкой мультиязычности"""
    return TextsStore.get(key, language_code=language_code)
