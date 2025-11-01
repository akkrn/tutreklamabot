"""Сервис для работы с рекуррентными платежами"""

import httpx
import structlog
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.models import Payment, Tariff, User, UserSubscription
from bot.services.payment_service import get_robokassa_client

logger = structlog.getLogger(__name__)


async def create_recurring_payment(
    user: User,
    tariff: Tariff,
    previous_payment: Payment,
    subscription: UserSubscription,
) -> Payment:
    """Создает рекуррентный платеж через Robokassa API"""

    # Генерируем новый invoice_id для рекуррентного платежа
    timestamp = int(timezone.now().timestamp())
    new_invoice_id = (
        (user.tg_user_id % 1000000) * 1000000
        + (tariff.id % 10000) * 100
        + (timestamp % 100)
    )

    # Проверяем уникальность invoice_id
    def check_invoice_id():
        return Payment.objects.filter(
            robokassa_invoice_id=new_invoice_id
        ).exists()

    exists = await sync_to_async(check_invoice_id)()

    if exists:
        # Если такой invoice_id уже существует, генерируем новый
        timestamp = int(timezone.now().timestamp()) + 1
        new_invoice_id = (
            (user.tg_user_id % 1000000) * 1000000
            + (tariff.id % 10000) * 100
            + (timestamp % 100)
        )

    client = get_robokassa_client()
    result = client.generate_subscription_link(
        inv_id=new_invoice_id,
        previous_inv_id=previous_payment.robokassa_invoice_id,
        out_sum=int(tariff.price),
    )

    # Получаем параметры из объекта params
    params = result.params

    # Формируем данные для POST запроса из параметров SDK
    post_data = {
        "MerchantLogin": params.merchant_login,
        "InvoiceID": str(params.inv_id),
        "PreviousInvoiceID": str(params.previous_inv_id),
        "Description": f"Автопродление подписки {tariff.name}",
        "SignatureValue": params.signature_value,
        "OutSum": str(params.out_sum),
    }

    # Добавляем IsTest если это тестовый режим
    if params.is_test:
        post_data["IsTest"] = "1"

    # URL для создания рекуррентного платежа в Robokassa
    robokassa_url = "https://auth.robokassa.ru/Merchant/Recurring"

    # Создаем запись о платеже в БД перед отправкой запроса
    def create_payment_record():
        return Payment.objects.create(
            user=user,
            tariff=tariff,
            subscription=subscription,
            robokassa_invoice_id=new_invoice_id,
            amount=tariff.price,
            previous_payment=previous_payment,
            status=Payment.STATUS_PENDING,
        )

    payment = await sync_to_async(create_payment_record)()

    try:
        # Отправляем POST запрос к API Robokassa для создания рекуррентного платежа
        async with httpx.AsyncClient() as client:
            response = await client.post(
                robokassa_url, data=post_data, timeout=30.0
            )
            response.raise_for_status()

            logger.info(
                "Рекуррентный платеж создан в Robokassa",
                user_id=user.tg_user_id,
                subscription_id=subscription.id,
                tariff_id=tariff.id,
                invoice_id=new_invoice_id,
                previous_invoice_id=previous_payment.robokassa_invoice_id,
                response_status=response.status_code,
            )

        return payment

    except httpx.HTTPError as e:
        # Если ошибка при создании платежа, помечаем его как неудачный
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
