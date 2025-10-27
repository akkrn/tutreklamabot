from datetime import timedelta

import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from robokassa import Robokassa

from bot.models import Tariff, User, UserSubscription

logger = structlog.getLogger(__name__)


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
) -> str:
    """
    Генерация URL для оплаты без создания записи в БД.
    Используется для формирования клавиатуры.
    """
    timestamp = int(timezone.now().timestamp())
    inv_id = (
        (user.tg_user_id % 1000000) * 1000000
        + (tariff.id % 10000) * 100
        + (timestamp % 100)
    )

    # Создаем клиент Robokassa
    client = get_robokassa_client()
    result = client.generate_open_payment_link(
        out_sum=tariff.price,
        inv_id=inv_id,
        description=f"Оплата тарифа {tariff.name}",
        recurring=True,
        user_id=user.tg_user_id,
        tariff_id=tariff.id,
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


async def process_payment_result(
    inv_id: str, out_sum: str, signature: str, **kwargs
) -> tuple[bool, str]:
    """Обработка результата платежа от Robokassa.
    Создает или продлевает подписку на основе inv_id.
    """
    # Конвертируем inv_id в int
    try:
        inv_id_int = int(inv_id)
    except ValueError:
        logger.error(
            "Некорректный формат inv_id",
            inv_id=inv_id,
        )
        return False, "invalid inv_id"

    # Проверяем подпись через SDK
    if not check_signature_result(inv_id, out_sum, signature, **kwargs):
        logger.error(
            "Некорректная подпись платежа",
            inv_id=inv_id,
            out_sum=out_sum,
        )
        return False, "invalid signature"

    user_id = kwargs.get("shp_user_id")
    tariff_id = kwargs.get("shp_tariff_id")

    if not user_id or not tariff_id:

        def get_subscription():
            try:
                return UserSubscription.objects.get(
                    robokassa_invoice_id=inv_id_int
                )
            except UserSubscription.DoesNotExist:
                return None

        subscription = await sync_to_async(get_subscription)()

        if not subscription:
            logger.error(
                "Подписка не найдена",
                inv_id=inv_id,
            )
            return False, "subscription not found"

        # Проверяем, что подписка еще не была оплачена
        if subscription.status == UserSubscription.STATUS_ACTIVE:
            logger.info(
                "Подписка уже была обработана",
                inv_id=inv_id,
            )
            return True, f"OK{inv_id}"

        # Активируем подписку
        def activate_subscription():
            subscription.status = UserSubscription.STATUS_ACTIVE
            subscription.started_at = timezone.now()
            subscription.expires_at = timezone.now() + timedelta(
                days=subscription.tariff.duration_days
            )
            subscription.save()

        await sync_to_async(activate_subscription)()

        logger.info(
            "Подписка активирована",
            inv_id=inv_id,
            user_id=subscription.user.tg_user_id,
            tariff_id=subscription.tariff.id,
            expires_at=subscription.expires_at,
        )

        return True, f"OK{inv_id}"

    def get_user():
        return User.objects.get(tg_user_id=user_id)

    def get_tariff():
        return Tariff.objects.get(id=tariff_id)

    user = await sync_to_async(get_user)()
    tariff = await sync_to_async(get_tariff)()

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

        return True, f"OK{inv_id}"
    else:
        # Создаем новую подписку
        def create_subscription():
            return UserSubscription.objects.create(
                user=user,
                tariff=tariff,
                status=UserSubscription.STATUS_ACTIVE,
                expires_at=timezone.now()
                + timedelta(days=tariff.duration_days),
                robokassa_invoice_id=inv_id_int,
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

        return True, f"OK{inv_id}"


async def create_or_extend_subscription(
    user: User, tariff: Tariff, inv_id: int
) -> UserSubscription:
    """
    Создание или продление подписки.
    Включает robokassa_invoice_id для отслеживания.
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
        # Создаем новую подписку
        def create_subscription():
            return UserSubscription.objects.create(
                user=user,
                tariff=tariff,
                status=UserSubscription.STATUS_ACTIVE,
                expires_at=timezone.now()
                + timedelta(days=tariff.duration_days),
                robokassa_invoice_id=inv_id,
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
