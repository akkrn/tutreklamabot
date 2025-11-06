import asyncio
import ipaddress

import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from bot.models import Payment, Tariff, User
from bot.services.payment_service import process_payment_result
from core.event_manager import EventType, event_manager
from core.redis_manager import redis_manager
from userbot.redis_messages import PaymentNotificationMessage

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
    shp_message_id = request.POST.get("shp_message_id", "")

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
        message_id=shp_message_id,
    )

    shp_user_id = int(shp_user_id) if shp_user_id else None
    shp_tariff_id = int(shp_tariff_id) if shp_tariff_id else None
    shp_message_id = int(shp_message_id) if shp_message_id else None
    is_master = True if shp_message_id else False

    inv_id_int = int(inv_id)
    amount_int = int(float(out_sum))

    try:
        user = User.objects.get(tg_user_id=shp_user_id)
        tariff = Tariff.objects.get(id=shp_tariff_id)
    except (User.DoesNotExist, Tariff.DoesNotExist) as e:
        logger.error(
            "Пользователь или тариф не найдены",
            inv_id=inv_id,
            user_id=shp_user_id,
            tariff_id=shp_tariff_id,
            error=str(e),
        )
        return HttpResponseBadRequest("user or tariff not found")

    payment, _ = Payment.objects.get_or_create(
        robokassa_invoice_id=inv_id_int,
        defaults={
            "user": user,
            "tariff": tariff,
            "amount": amount_int,
            "status": Payment.STATUS_PENDING,
            "message_id": shp_message_id,
            "is_master": is_master,
        },
    )

    if payment.status == Payment.STATUS_SUCCESS:
        logger.info(
            "Платеж уже успешно обработан, пропускаем повторную обработку",
            payment_id=payment.id,
            inv_id=inv_id,
        )
        return HttpResponse(f"OK{inv_id}", content_type="text/plain")

    payment_kwargs = {
        "shp_user_id": shp_user_id,
        "shp_tariff_id": shp_tariff_id,
    }
    if shp_message_id:
        payment_kwargs["shp_message_id"] = shp_message_id
    success, message, subscription = process_payment_result(
        inv_id=inv_id,
        out_sum=out_sum,
        signature=signature,
        **payment_kwargs,
    )

    if not success:
        logger.error(
            "Ошибка при обработке платежа",
            inv_id=inv_id,
            payment_id=payment.id,
            error=message,
        )

        payment.status = Payment.STATUS_FAILED
        payment.error_message = message
        payment.save()

        asyncio.run(
            _send_payment_notification_via_redis(payment, success=False)
        )
        return HttpResponseBadRequest(message)

    payment.status = Payment.STATUS_SUCCESS
    payment.processed_at = timezone.now()
    payment.subscription = subscription
    payment.save()

    if shp_message_id:
        asyncio.run(_send_payment_notification_via_redis(payment, success=True))
    return HttpResponse(f"OK{inv_id}", content_type="text/plain")


async def _send_payment_notification_via_redis(
    payment: Payment, success: bool
) -> None:
    """Отправляет уведомление о платеже через Redis"""

    try:
        await redis_manager.connect()

        def get_user_info():
            user = payment.user
            subscription = payment.subscription
            tariff = payment.tariff

            channels_count = user.subscribed_channels_count
            channels_limit = (
                subscription.tariff.channels_limit
                if subscription
                else tariff.channels_limit
            )

            return (
                user.tg_user_id,
                user.tg_chat_id or user.tg_user_id,
                tariff,
                channels_count,
                channels_limit,
            )

        (
            user_id,
            chat_id,
            tariff,
            channels_count,
            channels_limit,
        ) = await sync_to_async(get_user_info)()

        notification = PaymentNotificationMessage(
            user_id=user_id,
            payment_id=payment.id,
            success=success,
            chat_id=chat_id,
            message_id=payment.message_id,
            tariff_name=tariff.name,
            tariff_price=tariff.get_price_display(),
            tariff_duration_days=tariff.duration_days,
            channels_count=channels_count,
            channels_limit=channels_limit,
            error_message=payment.error_message if not success else None,
        )

        await event_manager.publish_event(
            EventType.PAYMENT_NOTIFICATION,
            notification,
            "bot:payment_notification",
        )

        logger.info(
            "Уведомление о платеже отправлено через Redis",
            payment_id=payment.id,
            user_id=user_id,
            success=success,
        )
    except Exception as e:
        logger.error(
            "Ошибка при отправке уведомления о платеже через Redis",
            error=str(e),
            payment_id=payment.id,
            exc_info=True,
        )


@csrf_exempt
@require_http_methods(["GET"])
def payment_fail(request):
    """Webhook для обработки fail-запросов от платежной системы."""
    inv_id = request.GET.get("InvId") or request.GET.get("inv_id")
    message_id = request.GET.get("shp_message_id")

    if not inv_id:
        logger.error(
            "Отсутствует обязательный параметр InvId/inv_id",
            get_data=dict(request.GET),
        )
        return

    try:
        inv_id_int = int(inv_id)
    except (ValueError, TypeError):
        logger.error(
            "Некорректный формат InvId",
            inv_id=inv_id,
        )
        return

    logger.info(
        "Получен fail-запрос для платежа",
        inv_id=inv_id_int,
        message_id=message_id,
    )

    error_message = request.GET.get("error_message") or "Платеж не был выполнен"
    if message_id:
        pass
    else:
        try:
            payment = Payment.objects.get(robokassa_invoice_id=inv_id_int)
        except Payment.DoesNotExist:
            logger.error(
                "Платеж не найден",
                inv_id=inv_id_int,
            )
            return

        # Если платеж уже успешно обработан, не меняем статус
        if payment.status == Payment.STATUS_SUCCESS:
            logger.info(
                "Платеж уже успешно обработан, пропускаем fail-запрос",
                payment_id=payment.id,
                inv_id=inv_id_int,
            )
            return

        # Обновляем статус платежа на failed
        payment.status = Payment.STATUS_FAILED
        payment.error_message = error_message
        payment.save()

        logger.info(
            "Платеж отмечен как failed",
            payment_id=payment.id,
            inv_id=inv_id_int,
            message_id=message_id,
            error_message=error_message,
        )
    return
