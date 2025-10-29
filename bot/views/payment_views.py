import ipaddress

import structlog
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from bot.handlers.helpers import send_file_message
from bot.keyboards import add_channels_with_menu_kb, payment_kb
from bot.models import User, UserSubscription
from bot.services.payment_service import (
    process_payment_result,
)

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

            # Отправляем уведомление об ошибке
            import asyncio

            chat_id = (
                subscription.user.tg_chat_id or subscription.user.tg_user_id
            )
            asyncio.create_task(
                send_payment_notification(
                    user_id=subscription.user.tg_user_id,
                    subscription_id=subscription.id,
                    success=False,
                    chat_id=chat_id,
                    message_id=shp_message_id,
                )
            )
        except UserSubscription.DoesNotExist:
            pass

        return HttpResponseBadRequest(message)

    # Отправляем уведомление об успешной оплате
    try:
        subscription = UserSubscription.objects.get(robokassa_invoice_id=inv_id)
        import asyncio

        chat_id = subscription.user.tg_chat_id or subscription.user.tg_user_id
        asyncio.create_task(
            send_payment_notification(
                user_id=subscription.user.tg_user_id,
                subscription_id=subscription.id,
                success=True,
                chat_id=chat_id,
                message_id=shp_message_id,
            )
        )
    except UserSubscription.DoesNotExist:
        pass

    return HttpResponse(message, content_type="text/plain")


async def send_payment_notification(
    user_id: int,
    subscription_id: int,
    success: bool,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> None:
    """Отправляет уведомление пользователю о результате оплаты."""
    # Импортируем здесь, чтобы избежать циклических зависимостей
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    try:

        def get_user_and_subscription():
            user = User.objects.get(tg_user_id=user_id)
            subscription = UserSubscription.objects.get(id=subscription_id)
            return user, subscription

        user, subscription = await sync_to_async(get_user_and_subscription)()

        if success:
            tariff = subscription.tariff
            channels_count = user.subscribed_channels_count
            channels_limit = subscription.tariff.channels_limit

            success_text = (
                f"✅ <b>Успешная оплата!</b> ✨\n\n"
                f"Следующее списание через {tariff.duration_days} дней — {tariff.get_price_display()}\n\n"
                f"Каналов добавлено: {channels_count}/{channels_limit}"
            )

            keyboard = add_channels_with_menu_kb()
            file_name = "payment_success.jpg"
        else:
            failed_text = "❌ Произошла ошибка, попробуйте снова"

            keyboard = payment_kb()
            success_text = failed_text
            file_name = "payment_failed.jpg"

        # Если есть message_id и chat_id, редактируем сообщение
        if message_id and chat_id:
            try:
                # Создаем фиктивный объект Message для использования в send_file_message
                class FakeMessage:
                    def __init__(
                        self, bot_instance, chat_id_val, message_id_val
                    ):
                        self.bot = bot_instance
                        self.chat = type("chat", (), {"id": chat_id_val})()
                        self.message_id = message_id_val
                        self.from_user = None

                fake_message = FakeMessage(bot, chat_id, message_id)

                await send_file_message(
                    message=fake_message,
                    file_name=file_name,
                    caption=success_text,
                    keyboard=keyboard,
                    bot=bot,
                    edit_message=True,
                )
            except Exception as e:
                logger.error(
                    "Ошибка при редактировании сообщения",
                    error=str(e),
                    chat_id=chat_id,
                    message_id=message_id,
                )
                # Если не удалось отредактировать, отправляем новое сообщение
                await bot.send_message(
                    chat_id=user_id,
                    text=success_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
        else:
            # Отправляем новое сообщение, если нет данных для редактирования
            await bot.send_message(
                chat_id=user_id,
                text=success_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
    finally:
        await bot.session.close()
