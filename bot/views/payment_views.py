import asyncio
import ipaddress

import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from bot.models import Payment, Tariff, User, UserSubscription
from bot.services.payment_service import process_payment_result
from core.event_manager import EventType, event_manager
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

    # Ищем или создаем платеж по invoice_id
    payment, created = Payment.objects.get_or_create(
        robokassa_invoice_id=inv_id_int,
        defaults={
            "user": user,
            "tariff": tariff,
            "amount": amount_int,
            "status": Payment.STATUS_PENDING,
            "message_id": shp_message_id,
        },
    )

    if not created:
        # Обновляем данные платежа, если он уже существует
        # Но не меняем статус, если платеж уже успешно обработан
        payment.amount = amount_int
        if shp_message_id:
            payment.message_id = shp_message_id

        # Обновляем статус только если платеж еще не обработан
        if payment.status not in [
            Payment.STATUS_SUCCESS,
            Payment.STATUS_FAILED,
        ]:
            payment.status = Payment.STATUS_PENDING

        payment.save()
        logger.info(
            "Обновлен существующий платеж",
            payment_id=payment.id,
            inv_id=inv_id,
            current_status=payment.status,
        )
    else:
        logger.info(
            "Создан новый платеж",
            payment_id=payment.id,
            inv_id=inv_id,
            user_id=user.tg_user_id,
            tariff_id=tariff.id,
        )

    # Проверяем, не обработан ли платеж уже
    if payment.status == Payment.STATUS_SUCCESS:
        logger.info(
            "Платеж уже успешно обработан, пропускаем повторную обработку",
            payment_id=payment.id,
            inv_id=inv_id,
        )
        # Отправляем уведомление (на случай если оно не было отправлено ранее)
        asyncio.run(_send_payment_notification_via_redis(payment, success=True))
        return HttpResponse(f"OK{inv_id}", content_type="text/plain")

    success, message = process_payment_result(
        inv_id=inv_id,
        out_sum=out_sum,
        signature=signature,
        shp_user_id=shp_user_id,
        shp_tariff_id=shp_tariff_id,
        shp_message_id=shp_message_id,
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

    try:
        # Ищем подписку по пользователю и тарифу (последнюю созданную)
        subscription = payment.user.get_subscription_for_tariff(payment.tariff)

        if not subscription:
            subscription = (
                UserSubscription.objects.filter(
                    user=payment.user,
                    tariff=payment.tariff,
                )
                .order_by("-created_at")
                .first()
            )

        if subscription:
            payment.subscription = subscription
        else:
            logger.warning(
                "Подписка не найдена для платежа",
                inv_id=inv_id,
                payment_id=payment.id,
                user_id=payment.user.tg_user_id,
                tariff_id=payment.tariff.id,
            )

    except Exception as e:
        logger.error(
            "Ошибка при поиске подписки для платежа",
            inv_id=inv_id,
            payment_id=payment.id,
            error=str(e),
            exc_info=True,
        )

    payment.save()

    asyncio.run(_send_payment_notification_via_redis(payment, success=True))
    return HttpResponse(f"OK{inv_id}", content_type="text/plain")


async def _send_payment_notification_via_redis(
    payment: Payment, success: bool
) -> None:
    """Отправляет уведомление о платеже через Redis"""
    try:

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
