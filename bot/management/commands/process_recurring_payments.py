# """Management команда для обработки рекуррентных платежей."""

# import asyncio
# from datetime import timedelta

# import structlog
# from django.core.management.base import BaseCommand
# from django.utils import timezone

# from bot.models import UserSubscription

# logger = structlog.getLogger(__name__)


# class Command(BaseCommand):
#     """
#     Обработка рекуррентных платежей.

#     Находит подписки, которые истекают в ближайшие дни,
#     и создает рекуррентные платежи для их автоматического продления.
#     """

#     help = "Обрабатывает рекуррентные платежи для подписок, которые истекают"

#     def add_arguments(self, parser):
#         parser.add_argument(
#             "--days-before-expiry",
#             type=int,
#             default=3,
#             help="Сколько дней до истечения подписки создавать рекуррентный платеж",
#         )

#     def handle(self, *args, **options):
#         days_before_expiry = options.get("days_before_expiry", 3)
#         logger.info(
#             "Запуск обработки рекуррентных платежей",
#             days_before_expiry=days_before_expiry,
#         )

#         asyncio.run(self.process_recurring_payments(days_before_expiry))

#     async def process_recurring_payments(self, days_before_expiry: int):
#         """Основная логика обработки рекуррентных платежей."""
#         expiry_date = timezone.now() + timedelta(days=days_before_expiry)

#         def get_expiring_subscriptions():
#             return list(
#                 UserSubscription.objects.filter(
#                     status=UserSubscription.STATUS_ACTIVE,
#                     expires_at__lte=expiry_date,
#                 ).select_related("user", "tariff")
#             )

#         expiring_subscriptions = await asyncio.get_event_loop().run_in_executor(
#             None, get_expiring_subscriptions
#         )

#         logger.info(
#             "Найдено подписок для обработки",
#             count=len(expiring_subscriptions),
#         )

#         for subscription in expiring_subscriptions:
#             await self._process_subscription(subscription)

#     async def _process_subscription(self, subscription: UserSubscription):
#         """Обработка одной подписки."""
#         user = subscription.user
#         tariff = subscription.tariff

#         if not subscription.is_recurring_enabled:
#             logger.info(
#                 "Автоплатеж отключен для этой подписки",
#                 user_id=user.tg_user_id,
#                 subscription_id=subscription.id,
#             )
#             return

#         def check_existing_payment():
#             last_payment = Payment.objects.filter(
#                 user=user,
#                 tariff=tariff,
#                 status=Payment.STATUS_SUCCESS,
#             ).order_by("-created_at").first()

#             if not last_payment:
#                 return False

#             return Payment.objects.filter(
#                 user=user,
#                 tariff=tariff,
#                 payment_type=Payment.PAYMENT_TYPE_RECURRENT,
#                 previous_payment=last_payment,
#                 status__in=[Payment.STATUS_PENDING, Payment.STATUS_SUCCESS],
#             ).exists()

#         exists = await asyncio.get_event_loop().run_in_executor(
#             None, check_existing_payment
#         )

#         if exists:
#             logger.info(
#                 "Рекуррентный платеж уже создан",
#                 user_id=user.tg_user_id,
#                 subscription_id=subscription.id,
#             )
#             return

#         # Создаем новый рекуррентный платеж
#         def create_recurring_payment():
#             import httpx
#             from bot.services.payment_service import (
#                 create_recurring_payment_post,
#             )
#             from django.conf import settings
#             from decimal import Decimal

#             # Генерируем новый invoice_id для рекуррентного платежа
#             timestamp = int(timezone.now().timestamp())
#             new_invoice_id = (user.tg_user_id % 1000000) * 1000000 + (
#                 tariff.id % 10000
#             ) * 100 + (timestamp % 100)

#             # Создаем POST данные для рекуррентного платежа
#             post_data = create_recurring_payment_post(
#                 merchant_login=settings.ROBOKASSA_MERCHANT_LOGIN,
#                 merchant_password_1=settings.ROBOKASSA_PASSWORD_1,
#                 cost=Decimal(tariff.price),
#                 invoice_id=new_invoice_id,
#                 previous_invoice_id=last_payment.robokassa_invoice_id,
#                 description=f"Продление подписки {tariff.name}",
#                 is_test=int(settings.ROBOKASSA_IS_TEST),
#             )

#             # Отправляем POST запрос на создание рекуррентного платежа
#             robokassa_url = "https://auth.robokassa.ru/Merchant/Recurring"

#             try:
#                 with httpx.Client() as client:
#                     response = client.post(robokassa_url, data=post_data)
#                     response.raise_for_status()

#                     # Создаем запись о платеже
#                     return Payment.objects.create(
#                         user=user,
#                         tariff=tariff,
#                         robokassa_invoice_id=new_invoice_id,
#                         amount=tariff.price,
#                         payment_url=robokassa_url,
#                         payment_type=Payment.PAYMENT_TYPE_RECURRENT,
#                         previous_payment=last_payment,
#                         status=Payment.STATUS_PENDING,
#                     )
#             except Exception as e:
#                 logger.error(
#                     "Ошибка при создании рекуррентного платежа",
#                     error=str(e),
#                     user_id=user.tg_user_id,
#                     tariff_id=tariff.id,
#                 )
#                 raise

#         recurring_payment = await asyncio.get_event_loop().run_in_executor(
#             None, create_recurring_payment
#         )

#         logger.info(
#             "Создан рекуррентный платеж",
#             user_id=user.tg_user_id,
#             subscription_id=subscription.id,
#             tariff_id=tariff.id,
#             amount=tariff.price,
#             invoice_id=recurring_payment.robokassa_invoice_id,
#         )
