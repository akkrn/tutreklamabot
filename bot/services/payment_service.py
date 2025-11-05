from datetime import timedelta

import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from robokassa import Robokassa

from bot.models import Payment, Tariff, User, UserSubscription

logger = structlog.getLogger(__name__)


def generate_invoice_id(
    user: User, tariff: Tariff, timestamp_offset: int = 0
) -> int:
    """
    Генерирует уникальный invoice_id для платежа.

    Формула: (user.tg_user_id % 1000000) * 1000000 + (tariff.id % 10000) * 100 + (timestamp % 100)
    """
    timestamp = int(timezone.now().timestamp()) + timestamp_offset
    return (
        (user.tg_user_id % 1000000) * 1000000
        + (tariff.id % 10000) * 100
        + (timestamp % 100)
    )


async def generate_unique_invoice_id(
    user: User, tariff: Tariff, max_retries: int = 10
) -> int:
    """
    Генерирует уникальный invoice_id с проверкой на существование в БД.

    Если invoice_id уже существует, генерирует новый с увеличенным timestamp_offset.
    """
    for offset in range(max_retries):
        invoice_id = generate_invoice_id(user, tariff, timestamp_offset=offset)

        def check_exists():
            return Payment.objects.filter(
                robokassa_invoice_id=invoice_id
            ).exists()

        exists = await sync_to_async(check_exists)()

        if not exists:
            return invoice_id

    raise ValueError(
        f"Не удалось сгенерировать уникальный invoice_id после {max_retries} попыток"
    )


def get_robokassa_client() -> Robokassa:
    """Создание клиента Robokassa SDK."""
    return Robokassa(
        merchant_login=settings.ROBOKASSA_MERCHANT_LOGIN,
        password1=settings.ROBOKASSA_PASSWORD_1,
        password2=settings.ROBOKASSA_PASSWORD_2,
        is_test=int(settings.ROBOKASSA_IS_TEST) == 1,
    )


def generate_payment_url_direct(
    user: User,
    tariff: Tariff,
    message_id: int | None = None,
) -> str:
    """
    Генерация URL для оплаты без создания записи в БД.
    Используется для формирования клавиатуры.
    """
    inv_id = generate_invoice_id(user, tariff)

    client = get_robokassa_client()
    result = client.generate_open_payment_link(
        out_sum=tariff.price,
        inv_id=inv_id,
        description=f"Оплата тарифа {tariff.name}",
        recurring=True,
        user_id=user.tg_user_id,
        tariff_id=tariff.id,
        message_id=message_id,
    )

    logger.info(
        "Сгенерирован URL для оплаты",
        user_id=user.tg_user_id,
        tariff_id=tariff.id,
        inv_id=inv_id,
    )

    return result.url


def check_signature_result(
    order_number: str, received_sum: str, received_signature: str, **kwargs
) -> bool:
    """Проверка подписи при получении уведомления от Robokassa через SDK."""
    try:
        client = get_robokassa_client()
        return client.is_result_notification_valid(
            signature=received_signature,
            out_sum=received_sum,
            inv_id=order_number,
            **kwargs,
        )
    except Exception as e:
        logger.error(
            "Ошибка при проверке подписи Robokassa",
            error=str(e),
            order_number=order_number,
        )
        return False


def process_payment_result(
    inv_id: int, out_sum: int, signature: str, **kwargs
) -> tuple[bool, str]:
    """Обработка результата платежа от Robokassa.
    Создает или продлевает подписку."""

    if not check_signature_result(inv_id, out_sum, signature, **kwargs):
        logger.error(
            "Некорректная подпись платежа",
            inv_id=inv_id,
            out_sum=out_sum,
        )
        return False, "invalid signature"

    user_id = kwargs.get("shp_user_id")
    tariff_id = kwargs.get("shp_tariff_id")

    try:
        user = User.objects.get(tg_user_id=user_id)
        tariff = Tariff.objects.get(id=tariff_id)
    except (User.DoesNotExist, Tariff.DoesNotExist) as e:
        logger.error(
            "Пользователь или тариф не найдены",
            inv_id=inv_id,
            user_id=user_id,
            tariff_id=tariff_id,
            error=str(e),
        )
        return False, "user or tariff not found", None

    subscription = user.get_subscription_for_tariff(tariff)

    if subscription:
        subscription.expires_at += timedelta(days=tariff.duration_days)
        subscription.status = UserSubscription.STATUS_ACTIVE
        subscription.save()

        logger.info(
            "Подписка продлена",
            user_id=user.tg_user_id,
            tariff_id=tariff.id,
            subscription_id=subscription.id,
            expires_at=subscription.expires_at,
        )
    else:
        subscription = UserSubscription.objects.create(
            user=user,
            tariff=tariff,
            status=UserSubscription.STATUS_ACTIVE,
            expires_at=timezone.now() + timedelta(days=tariff.duration_days),
            is_recurring_enabled=True,
        )

        logger.info(
            "Создана новая подписка",
            user_id=user.tg_user_id,
            tariff_id=tariff.id,
            subscription_id=subscription.id,
            expires_at=subscription.expires_at,
        )

    return True, f"OK{inv_id}", subscription


async def create_or_extend_subscription(
    user: User, tariff: Tariff, inv_id: int
) -> UserSubscription:
    """
    Создание или продление подписки.
    """

    # Проверяем, есть ли активная подписка на этот тариф
    def get_active_subscription():
        return UserSubscription.objects.filter(
            user=user, tariff=tariff, status=UserSubscription.STATUS_ACTIVE
        ).first()

    active_subscription = await sync_to_async(get_active_subscription)()

    if active_subscription:
        # Продлеваем существующую подписку
        def extend_subscription():
            active_subscription.expires_at += timedelta(
                days=tariff.duration_days
            )
            active_subscription.is_recurring_enabled = True
            active_subscription.status = UserSubscription.STATUS_ACTIVE
            active_subscription.is_recurring_enabled = True
            active_subscription.save()

        await sync_to_async(extend_subscription)()

        logger.info(
            "Подписка продлена",
            user_id=user.tg_user_id,
            tariff_id=tariff.id,
            expires_at=active_subscription.expires_at,
        )
        return active_subscription
    else:

        def create_subscription():
            return UserSubscription.objects.create(
                user=user,
                tariff=tariff,
                status=UserSubscription.STATUS_ACTIVE,
                expires_at=timezone.now()
                + timedelta(days=tariff.duration_days),
                is_recurring_enabled=True,
            )

        subscription = await sync_to_async(create_subscription)()

        logger.info(
            "Создана новая подписка",
            user_id=user.tg_user_id,
            tariff_id=tariff.id,
            subscription_id=subscription.id,
            inv_id=inv_id,
            expires_at=subscription.expires_at,
        )
        return subscription


async def cancel_recurring(user: User) -> bool:
    """Отключение автоплатежа для пользователя."""

    def get_active_subscription():
        return UserSubscription.objects.filter(
            user=user,
            status=UserSubscription.STATUS_ACTIVE,
            is_recurring_enabled=True,
        ).first()

    active_subscription = await sync_to_async(get_active_subscription)()

    if not active_subscription:
        logger.warning(
            "Активная подписка с автоплатежом не найдена",
            user_id=user.tg_user_id,
        )
        return False

    def disable_recurring():
        active_subscription.is_recurring_enabled = False
        active_subscription.save()

    await sync_to_async(disable_recurring)()

    logger.info(
        "Автоплатеж отключен",
        user_id=user.tg_user_id,
        subscription_id=active_subscription.id,
    )

    return True


async def get_user_subscription_info(user: User) -> dict:
    """Получение информации о подписке пользователя."""

    def get_subscription():
        return UserSubscription.objects.filter(
            user=user, status=UserSubscription.STATUS_ACTIVE
        ).first()

    subscription = await sync_to_async(get_subscription)()

    if not subscription:
        return {
            "has_subscription": False,
            "tariff_name": "Нет подписки",
            "expires_at": None,
            "days_remaining": 0,
        }

    return {
        "has_subscription": True,
        "tariff_name": subscription.tariff.name,
        "expires_at": subscription.expires_at,
        "days_remaining": subscription.days_remaining,
    }
