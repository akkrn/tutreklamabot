import ipaddress

import structlog
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from bot.models import UserSubscription

logger = structlog.getLogger(__name__)


def is_ip_allowed(client_ip: str) -> bool:
    """Проверка, разрешен ли IP адрес для webhook запросов."""
    allowed_ips = getattr(settings, "ROBOKASSA_ALLOWED_IPS", [])

    try:
        client_ip_obj = ipaddress.ip_address(client_ip)
        for allowed_ip in allowed_ips:
            if "/" in allowed_ip:
                network = ipaddress.ip_network(allowed_ip, strict=False)
                if client_ip_obj in network:
                    return True
            elif str(allowed_ip) == client_ip:
                return True
        return False
    except ValueError:
        logger.error("Некорректный IP адрес", ip=client_ip)
        return False


def process_payment(
    inv_id: str,
    out_sum: str,
    signature: str,
    shp_user_id: int | None = None,
    shp_tariff_id: int | None = None,
) -> tuple[bool, str]:
    """Обработка платежа через payment_service."""
    from bot.services.payment_service import (
        check_signature_result,
        process_payment_result,
    )

    # Проверяем подпись
    if not check_signature_result(
        inv_id,
        out_sum,
        signature,
        shp_user_id=shp_user_id,
        shp_tariff_id=shp_tariff_id,
    ):
        logger.error(
            "Некорректная подпись платежа",
            inv_id=inv_id,
        )
        return False, "invalid signature"

    # Обрабатываем платеж через payment_service
    success, message = process_payment_result(
        inv_id=inv_id,
        out_sum=out_sum,
        signature=signature,
        shp_user_id=shp_user_id,
        shp_tariff_id=shp_tariff_id,
    )

    return success, message


@csrf_exempt
@require_http_methods(["POST"])
def robokassa_result(request):
    """Webhook для обработки уведомлений от Robokassa (ResultURL)."""
    client_ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[
        0
    ] or request.META.get("REMOTE_ADDR", "")

    if not is_ip_allowed(client_ip):
        logger.warning(
            "Запрос с неразрешенного IP адреса",
            ip=client_ip,
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return HttpResponseBadRequest("IP not allowed")

    inv_id = request.POST.get("InvId", "")
    out_sum = request.POST.get("OutSum", "")
    signature = request.POST.get("SignatureValue", "")
    shp_user_id = request.POST.get("shp_user_id", "")
    shp_tariff_id = request.POST.get("shp_tariff_id", "")

    # TODO собирать email = request.POST.get("EMail", "")
    if not all([inv_id, out_sum, signature]):
        logger.error(
            "Отсутствуют обязательные параметры",
            inv_id=inv_id,
            out_sum=out_sum,
            signature=signature,
        )
        return HttpResponseBadRequest("Missing required parameters")

    logger.info(
        "Получено уведомление от Robokassa",
        inv_id=inv_id,
        out_sum=out_sum,
        client_ip=client_ip,
        user_id=shp_user_id,
        tariff_id=shp_tariff_id,
    )

    shp_user_id = int(shp_user_id) if shp_user_id else None
    shp_tariff_id = int(shp_tariff_id) if shp_tariff_id else None

    success, message = process_payment(
        inv_id=inv_id,
        out_sum=out_sum,
        signature=signature,
        shp_user_id=shp_user_id,
        shp_tariff_id=shp_tariff_id,
    )

    if not success:
        logger.error(
            "Ошибка при обработке платежа",
            inv_id=inv_id,
            message=message,
        )

        # Отмечаем подписку как неудачную
        try:
            subscription = UserSubscription.objects.get(
                robokassa_invoice_id=inv_id
            )
            subscription.status = UserSubscription.STATUS_UNPAID
            subscription.save()

            logger.info(
                "Подписка переведена в статус 'Неоплачена'",
                subscription_id=subscription.id,
            )
        except UserSubscription.DoesNotExist:
            pass

        return HttpResponseBadRequest(message)

    return HttpResponse(message, content_type="text/plain")
