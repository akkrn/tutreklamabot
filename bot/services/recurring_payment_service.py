"""Сервис для работы с рекуррентными платежами"""

import hashlib

import httpx
import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from bot.models import Payment, Tariff, User, UserSubscription

logger = structlog.getLogger(__name__)


def calculate_recurring_signature(
    merchant_login: str,
    out_sum: float,
    inv_id: int,
    previous_inv_id: int,
    password: str,
) -> str:
    """Вычисляет подпись для рекуррентного платежа"""
    signature_string = (
        f"{merchant_login}:{out_sum}:{inv_id}:{previous_inv_id}:{password}"
    )
    return hashlib.md5(signature_string.encode()).hexdigest()


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

    # Вычисляем подпись для рекуррентного платежа
    signature = calculate_recurring_signature(
        merchant_login=settings.ROBOKASSA_MERCHANT_LOGIN,
        out_sum=float(tariff.price),
        inv_id=new_invoice_id,
        previous_inv_id=previous_payment.robokassa_invoice_id,
        password=settings.ROBOKASSA_PASSWORD_1,
    )

    # Формируем данные для POST запроса
    post_data = {
        "MerchantLogin": settings.ROBOKASSA_MERCHANT_LOGIN,
        "OutSum": str(tariff.price),
        "InvId": str(new_invoice_id),
        "PreviousInvId": str(previous_payment.robokassa_invoice_id),
        "SignatureValue": signature,
        "Description": f"Автопродление подписки {tariff.name}",
        "IsTest": settings.ROBOKASSA_IS_TEST,
    }

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
