import os

from dotenv import load_dotenv

from .base import *  # noqa

# Загружаем переменные окружения из .env файла
load_dotenv()

# Безопасность
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable is required")

# Разрешенные хосты
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost").split(",")

# База данных PostgreSQL
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PG_NAME", "tutreklama"),
        "USER": os.environ.get("PG_USER", "postgres"),
        "PASSWORD": os.environ.get("PG_PASS"),
        "HOST": os.environ.get("PG_HOST", "db"),
        "PORT": os.environ.get("PG_PORT", "5432"),
        "OPTIONS": {
            "sslmode": "prefer",
        },
    }
}

# Redis настройки
BOT_REDIS_HOST = os.environ.get("BOT_REDIS_HOST", "redis")
BOT_REDIS_PORT = int(os.environ.get("BOT_REDIS_PORT", "6379"))
BOT_REDIS_DB = int(os.environ.get("BOT_REDIS_DB", "0"))

# Telegram настройки
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Настройки таймаутов для Telegram Bot API
BOT_TIMEOUT_TOTAL = int(
    os.environ.get("BOT_TIMEOUT_TOTAL", "60")
)  # Общий таймаут в секундах
BOT_TIMEOUT_CONNECT = int(
    os.environ.get("BOT_TIMEOUT_CONNECT", "30")
)  # Таймаут подключения
BOT_TIMEOUT_SOCK_READ = int(
    os.environ.get("BOT_TIMEOUT_SOCK_READ", "30")
)  # Таймаут чтения

# Support
SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "support_username")

# Статические файлы
STATIC_URL = "/static/"
STATIC_ROOT = "/app/staticfiles"
STATICFILES_DIRS = []

# Медиа файлы
MEDIA_URL = "/media/"
MEDIA_ROOT = "/app/media"

# Настройки для обслуживания статических файлов в продакшене
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
# Кеширование
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"redis://{BOT_REDIS_HOST}:{BOT_REDIS_PORT}/{BOT_REDIS_DB}",
    }
}

# Сессии
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# Безопасность
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# HTTPS настройки (если используется)
if os.environ.get("USE_HTTPS", "False").lower() == "true":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# CORS настройки (если нужны)
CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
CORS_ALLOWED_ORIGINS = [
    origin.strip() for origin in CORS_ALLOWED_ORIGINS if origin.strip()
]

# Robokassa настройки
ROBOKASSA_MERCHANT_LOGIN = os.environ.get("ROBOKASSA_MERCHANT_LOGIN", "")
ROBOKASSA_PASSWORD1 = os.environ.get("ROBOKASSA_PASSWORD1", "")
ROBOKASSA_PASSWORD2 = os.environ.get("ROBOKASSA_PASSWORD2", "")
ROBOKASSA_TEST_MODE = (
    os.environ.get("ROBOKASSA_TEST_MODE", "True").lower() == "true"
)

ROBOKASSA_RESULT_URL = os.environ.get("ROBOKASSA_RESULT_URL", "")

TELEGRAM_BOT_USERNAME = "TUTreklamabot"
