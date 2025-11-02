import asyncio
from datetime import timedelta

import structlog
from asgiref.sync import sync_to_async
from celery import shared_task
from django.utils import timezone

from bot.models import Payment, UserSubscription
from bot.services.recurring_payment_service import create_recurring_payment

logger = structlog.getLogger(__name__)


@shared_task
def process_recurring_payments_task(
    days_before_expiry: int = 3, user_id: int | None = None
):
    """Celery задача для обработки рекуррентных платежей"""
    logger.info(
        "Запуск обработки рекуррентных платежей",
        days_before_expiry=days_before_expiry,
        user_id=user_id,
    )

    asyncio.run(process_recurring_payments(days_before_expiry, user_id))


async def process_recurring_payments(
    days_before_expiry: int, user_id: int | None = None
):
    """Основная логика обработки рекуррентных платежей"""

    def get_expiring_subscriptions():
        """Получает подписки, которые истекают в ближайшие дни"""
        queryset = UserSubscription.objects.filter(
            status=UserSubscription.STATUS_ACTIVE,
            is_recurring_enabled=True,
        )

        # Если указан user_id, фильтруем только по нему (для тестирования)
        if user_id:
            queryset = queryset.filter(user__tg_user_id=user_id)
        else:
            # Если user_id не указан, фильтруем по дате истечения
            expiry_date = timezone.now() + timedelta(days=days_before_expiry)
            expiry_start = timezone.now() + timedelta(days=1)
            queryset = queryset.filter(
                expires_at__gte=expiry_start,
                expires_at__lte=expiry_date,
            )

        return list(
            queryset.select_related("user", "tariff").order_by("expires_at")
        )

    expiring_subscriptions = await sync_to_async(get_expiring_subscriptions)()

    logger.info(
        "Найдено подписок для обработки",
        count=len(expiring_subscriptions),
        days_before_expiry=days_before_expiry,
    )

    processed_count = 0
    skipped_count = 0
    error_count = 0

    for subscription in expiring_subscriptions:
        try:
            result = await process_subscription_recurring_payment(subscription)
            if result["success"]:
                processed_count += 1
            else:
                skipped_count += 1
                logger.warning(
                    "Подписка пропущена",
                    subscription_id=subscription.id,
                    reason=result.get("reason"),
                )
        except Exception as e:
            error_count += 1
            logger.error(
                "Ошибка при обработке подписки",
                subscription_id=subscription.id,
                error=str(e),
                exc_info=True,
            )

    logger.info(
        "Обработка рекуррентных платежей завершена",
        processed=processed_count,
        skipped=skipped_count,
        errors=error_count,
        total=len(expiring_subscriptions),
    )


async def process_subscription_recurring_payment(
    subscription: UserSubscription,
) -> dict:
    """Обработка рекуррентного платежа для одной подписки"""

    user = subscription.user
    tariff = subscription.tariff

    # Проверяем, есть ли уже активный рекуррентный платеж
    def check_existing_payment():
        last_payment = (
            Payment.objects.filter(
                user=user,
                tariff=tariff,
                status=Payment.STATUS_SUCCESS,
            )
            .order_by("-created_at")
            .first()
        )

        if not last_payment:
            return False, None

        master_payment = (
            Payment.objects.filter(
                user=user,
                tariff=tariff,
                is_master=True,
                status=Payment.STATUS_SUCCESS,
            )
            .order_by("-created_at")
            .first()
        )

        if not master_payment:
            return False, None

        existing = Payment.objects.filter(
            user=user,
            tariff=tariff,
            previous_payment=master_payment,
            status__in=[Payment.STATUS_PENDING, Payment.STATUS_SUCCESS],
        ).exists()

        return existing, master_payment

    exists, last_payment = await sync_to_async(check_existing_payment)()

    if exists:
        return {
            "success": False,
            "reason": "Рекуррентный платеж уже создан",
        }

    if not last_payment:
        return {
            "success": False,
            "reason": "Нет предыдущего успешного платежа",
        }

    # Создаем новый рекуррентный платеж
    try:
        payment = await create_recurring_payment(
            user=user,
            tariff=tariff,
            subscription=subscription,
        )

        logger.info(
            "Создан рекуррентный платеж",
            user_id=user.tg_user_id,
            subscription_id=subscription.id,
            tariff_id=tariff.id,
            amount=tariff.price,
            invoice_id=payment.robokassa_invoice_id,
            payment_id=payment.id,
        )

        return {"success": True, "payment_id": payment.id}

    except Exception as e:
        logger.error(
            "Ошибка при создании рекуррентного платежа",
            user_id=user.tg_user_id,
            subscription_id=subscription.id,
            tariff_id=tariff.id,
            error=str(e),
            exc_info=True,
        )
        return {"success": False, "reason": f"Ошибка: {str(e)}"}
