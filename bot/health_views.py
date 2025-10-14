"""
Health check views для мониторинга состояния приложения.
"""

import redis
import structlog
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = structlog.getLogger(__name__)


@require_http_methods(["GET"])
@csrf_exempt
def health_check(request):
    """
    Простая проверка здоровья приложения.
    """
    return JsonResponse({"status": "ok", "service": "tutreklama"})


@require_http_methods(["GET"])
@csrf_exempt
def health_detailed(request):
    """
    Детальная проверка здоровья всех компонентов.
    """
    health_status = {"status": "ok", "service": "tutreklama", "checks": {}}

    # Проверка базы данных
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        health_status["checks"]["database"] = {"status": "ok"}
    except Exception as e:
        health_status["checks"]["database"] = {
            "status": "error",
            "error": str(e),
        }
        health_status["status"] = "error"

    # Проверка Redis
    try:
        cache.set("health_check", "ok", 10)
        cache_result = cache.get("health_check")
        if cache_result == "ok":
            health_status["checks"]["redis"] = {"status": "ok"}
        else:
            health_status["checks"]["redis"] = {
                "status": "error",
                "error": "Cache test failed",
            }
            health_status["status"] = "error"
    except Exception as e:
        health_status["checks"]["redis"] = {"status": "error", "error": str(e)}
        health_status["status"] = "error"

    # Проверка Redis подключения напрямую
    try:
        from django.conf import settings

        redis_client = redis.Redis(
            host=getattr(settings, "BOT_REDIS_HOST", "localhost"),
            port=getattr(settings, "BOT_REDIS_PORT", 6379),
            db=getattr(settings, "BOT_REDIS_DB", 0),
            socket_connect_timeout=5,
        )
        redis_client.ping()
        health_status["checks"]["redis_direct"] = {"status": "ok"}
    except Exception as e:
        health_status["checks"]["redis_direct"] = {
            "status": "error",
            "error": str(e),
        }
        health_status["status"] = "error"

    # Проверка Telegram API (базовая)
    try:
        from django.conf import settings

        if hasattr(settings, "BOT_TOKEN") and settings.BOT_TOKEN:
            health_status["checks"]["telegram_api"] = {"status": "ok"}
        else:
            health_status["checks"]["telegram_api"] = {
                "status": "warning",
                "message": "BOT_TOKEN not configured",
            }
    except Exception as e:
        health_status["checks"]["telegram_api"] = {
            "status": "error",
            "error": str(e),
        }
        health_status["status"] = "error"

    # Определяем HTTP статус код
    status_code = 200 if health_status["status"] == "ok" else 503

    return JsonResponse(health_status, status=status_code)


@require_http_methods(["GET"])
@csrf_exempt
def health_ready(request):
    """
    Проверка готовности к работе (для Kubernetes readiness probe).
    """
    try:
        # Проверяем только критически важные компоненты
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

        cache.set("ready_check", "ok", 5)
        cache_result = cache.get("ready_check")

        if cache_result == "ok":
            return JsonResponse({"status": "ready"})
        else:
            return JsonResponse({"status": "not ready"}, status=503)

    except Exception as e:
        logger.error("Readiness check failed", error=str(e))
        return JsonResponse(
            {"status": "not ready", "error": str(e)}, status=503
        )


@require_http_methods(["GET"])
@csrf_exempt
def health_live(request):
    """
    Проверка живости приложения (для Kubernetes liveness probe).
    """
    return JsonResponse({"status": "alive"})
