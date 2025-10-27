"""Handlers для обработки результатов оплаты через WebApp."""

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
)
from asgiref.sync import sync_to_async

from bot.handlers.helpers import get_menu, send_file_message
from bot.keyboards import add_channels_with_menu_kb, payment_kb
from bot.middlewares import current_user

router = Router()
logger = structlog.getLogger(__name__)


@router.callback_query(F.data == "success_payment")
async def handle_success_payment(callback: CallbackQuery, state: FSMContext):
    """Handler для успешной оплаты."""
    logger.info(
        "Пользователь успешно завершил оплату",
        user_id=callback.from_user.id,
    )

    user = current_user.get()

    def get_subscription_info():
        subscription = (
            user.subscriptions.filter(status="active")
            .order_by("-created_at")
            .first()
        )
        if subscription:
            return subscription
        return None

    subscription = await sync_to_async(get_subscription_info)()

    if subscription:
        tariff = subscription.tariff
        channels_count = user.subscribed_channels_count
        channels_limit = subscription.tariff.channels_limit

        success_text = (
            f"✅ <b>Успешная оплата!</b> ✨\n\n"
            f"Следующее списание через {tariff.duration_days} дней — {tariff.get_price_display()}\n\n"
            f"Каналов добавлено: {channels_count}/{channels_limit}"
        )
        await send_file_message(
            message=callback.message,
            file_name="payment_success.jpg",
            caption=success_text,
            keyboard=add_channels_with_menu_kb(),
            edit_message=True,
        )
    else:
        await get_menu(callback.message, state, is_from_callback=True)


@router.callback_query(F.data == "failed_payment")
async def handle_failed_payment(callback: CallbackQuery, state: FSMContext):
    """Handler для неудачной оплаты."""
    logger.info(
        "Пользователь отменил или не завершил оплату",
        user_id=callback.from_user.id,
    )

    failed_text = "❌ Произошла ошибка, попробуйте снова"

    await send_file_message(
        message=callback.message,
        file_name="payment_failed.jpg",
        caption=failed_text,
        keyboard=payment_kb(),
        edit_message=True,
    )
