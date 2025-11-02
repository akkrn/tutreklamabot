"""Сервис для работы с рекуррентными платежами"""

import httpx
import structlog
from aiogram import Bot
from aiogram.enums import ParseMode
from asgiref.sync import sync_to_async
from django.conf import settings

from bot.handlers.helpers import send_file_message
from bot.handlers.payment_notification_handler import (
    PaymentNotificationHandler,
    create_message_from_notification,
)
from bot.keyboards import payment_kb
from bot.models import Payment, Tariff, User, UserSubscription
from bot.services.payment_service import (
    generate_unique_invoice_id,
    get_robokassa_client,
)
from userbot.redis_messages import PaymentNotificationMessage

logger = structlog.getLogger(__name__)


async def send_payment_error_notification(
    payment: Payment,
) -> None:
    """Отправляет уведомление об ошибке платежа с изображением"""

    try:
        user = await User.objects.aget(id=payment.user_id)
        chat_id = user.tg_chat_id or user.tg_user_id

        notification = PaymentNotificationMessage(
            user_id=user.tg_user_id,
            payment_id=payment.id,
            success=False,
            chat_id=chat_id,
            message_id=payment.message_id,
            tariff_name=payment.tariff.name,
            tariff_price=payment.tariff.get_price_display(),
            tariff_duration_days=payment.tariff.duration_days,
            channels_count=user.subscribed_channels_count,
            channels_limit=payment.tariff.channels_limit,
            error_message=payment.error_message,
        )

        handler = PaymentNotificationHandler(None)  # bot будет передан ниже
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        handler.bot = bot

        message = await create_message_from_notification(notification, bot)
        if message:
            error_text = (
                payment.error_message
                if payment.error_message
                else "<b>❌ Произошла ошибка при платеже, попробуйте снова</b>"
            )

            await send_file_message(
                message=message,
                file_name="failed_payment.jpg",
                caption=error_text,
                keyboard=payment_kb(),
                bot=bot,
                parse_mode=ParseMode.HTML,
            )

        await bot.session.close()
    except Exception as e:
        logger.error(
            "Ошибка при отправке уведомления об ошибке платежа",
            payment_id=payment.id,
            error=str(e),
            exc_info=True,
        )


async def create_recurring_payment(
    user: User,
    tariff: Tariff,
    subscription: UserSubscription,
) -> Payment:
    """Создает рекуррентный платеж через Robokassa API"""

    new_invoice_id = await generate_unique_invoice_id(user, tariff)

    client = get_robokassa_client()
    result = client.generate_open_payment_link(
        out_sum=tariff.price,
        inv_id=new_invoice_id,
        description=f"Автопродление подписки {tariff.name}",
        recurring=True,
        user_id=user.tg_user_id,
        tariff_id=tariff.id,
    )

    def get_master_payment():
        return (
            Payment.objects.filter(
                user=user,
                tariff=tariff,
                is_master=True,
                status=Payment.STATUS_SUCCESS,
            )
            .order_by("-created_at")
            .first()
        )

    master_payment = await sync_to_async(get_master_payment)()

    if not master_payment:
        error_msg = "Материнский платеж не найден"
        logger.error(
            error_msg,
            user_id=user.tg_user_id,
            tariff_id=tariff.id,
        )
        raise ValueError(error_msg)

    params = result.params
    post_data = {
        "MerchantLogin": params.merchant_login,
        "InvoiceID": str(params.inv_id),
        "PreviousInvoiceID": str(master_payment.robokassa_invoice_id),
        "Description": params.description,
        "SignatureValue": params.signature_value,
        "OutSum": str(params.out_sum),
    }

    if params.additional_params:
        post_data.update(params.additional_params)

    robokassa_url = "https://auth.robokassa.ru/Merchant/Recurring"

    def create_payment_record():
        return Payment.objects.create(
            user=user,
            tariff=tariff,
            subscription=subscription,
            robokassa_invoice_id=new_invoice_id,
            amount=tariff.price,
            previous_payment=master_payment,
            status=Payment.STATUS_PENDING,
        )

    payment = await sync_to_async(create_payment_record)()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                robokassa_url, data=post_data, timeout=30.0
            )
            response.raise_for_status()

            response_text = response.text.strip()

            logger.info(
                "Ответ от Robokassa на рекуррентный платеж",
                user_id=user.tg_user_id,
                subscription_id=subscription.id,
                tariff_id=tariff.id,
                invoice_id=new_invoice_id,
            )
            expected_success_response = f"OK{new_invoice_id}"

            def update_payment_status():
                if response_text == expected_success_response:
                    logger.info(
                        "Рекуррентный платеж успешно создан",
                        payment_id=payment.id,
                        invoice_id=new_invoice_id,
                    )
                    return True
                else:
                    payment.status = Payment.STATUS_FAILED
                    payment.error_message = f"Ошибка Robokassa: {response_text}"
                    payment.save()

                    logger.error(
                        "Рекуррентный платеж не прошел",
                        payment_id=payment.id,
                        invoice_id=new_invoice_id,
                        response_text=response_text,
                        expected=expected_success_response,
                    )
                    return False

            is_success = await sync_to_async(update_payment_status)()

            if not is_success:
                await send_payment_error_notification(payment)

        return payment

    except httpx.HTTPError as e:

        def mark_payment_failed(error: httpx.HTTPError):
            payment.status = Payment.STATUS_FAILED
            payment.error_message = f"Ошибка API Robokassa: {str(error)}"
            payment.save()

        await sync_to_async(mark_payment_failed)(error=e)

        logger.error(
            "Ошибка при создании рекуррентного платежа через API",
            user_id=user.tg_user_id,
            subscription_id=subscription.id,
            tariff_id=tariff.id,
            invoice_id=new_invoice_id,
            error=str(e),
            exc_info=True,
        )

        await send_payment_error_notification(payment)

        raise

    except Exception as e:
        logger.error(
            "Неожиданная ошибка при создании рекуррентного платежа",
            user_id=user.tg_user_id,
            subscription_id=subscription.id,
            tariff_id=tariff.id,
            error=str(e),
            exc_info=True,
        )

        raise
