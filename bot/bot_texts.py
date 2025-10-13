import threading
import time

import structlog

from bot.default_texts import DEFAULT_TEXTS
from bot.models import TextTemplate
from utils.db import check_django_connection

logger = structlog.get_logger(__name__)


class TextsStore:
    _texts: dict[str, dict[str, str]] = {}
    _cache_ttl = 600  # 10 минут
    _last_load_time = 0

    @classmethod
    def get(cls, key: str) -> str:
        """Получить текст по ключу для указанного языка"""
        current_time = time.time()
        if (
            not cls._texts
            or (current_time - cls._last_load_time) > cls._cache_ttl
        ):
            thread = threading.Thread(target=cls._load_texts)
            thread.start()
            thread.join()
        return cls._texts.get(key, DEFAULT_TEXTS.get(key, key))

    @classmethod
    def _load_texts(cls):
        texts_by_key = {}

        try:
            text_templates = TextTemplate.objects.all()
            for template in text_templates:
                texts_by_key[template.text_key] = template.default_text
        except Exception:
            logger.exception("Ошибка загрузки текстов из БД")

        cls._texts = texts_by_key
        cls._last_load_time = time.time()

    @classmethod
    def initialize(cls):
        """Инициализация текстов при запуске"""
        try:
            check_django_connection()
            cls._load_texts()
        except Exception:
            logger.exception("Ошибка при инициализации текстов")
            cls._texts = DEFAULT_TEXTS
