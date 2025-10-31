"""Обработчик уведомлений о платежах из Redis"""

from datetime import datetime
from pathlib import Path

import structlog
from aiogram import Bot
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, Message
from aiogram.types import User as TgUser
from django.conf import settings
from django.utils import timezone

from bot.constants import MEDIA_FILES_PATH
from bot.handlers.helpers import send_file_message
from bot.keyboards import add_channels_with_menu_kb, payment_kb
from bot.models import User
from bot.tools import send_file
from userbot.redis_messages import PaymentNotificationMessage

logger = structlog.getLogger(__name__)


async def create_message_from_notification(
    notification: PaymentNotificationMessage, bot: Bot
) -> Message | None:
    """Создает реальный объект Message от aiogram из уведомления с реальными данными пользователя из БД"""
    if not notification.chat_id:
        return None

    try:
        user = await User.objects.aget(tg_user_id=notification.user_id)
    except User.DoesNotExist:
        logger.warning(
            f"Пользователь с ID {notification.user_id} не найден в БД"
        )
        return None

    chat = Chat(
        id=notification.chat_id,
        type=ChatType.PRIVATE,
    )

    from_user = TgUser(
        id=notification.user_id,
        is_bot=False,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
    )

    message_id = notification.message_id if notification.message_id else 0

    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=from_user,
    )


class PaymentNotificationHandler:
    """Обработчик уведомлений о платежах"""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def handle_payment_notification(
        self, notification: PaymentNotificationMessage
    ):
        """Обрабатывает уведомление о платеже"""
        logger.info(
            "Получено уведомление о платеже",
            user_id=notification.user_id,
            payment_id=notification.payment_id,
            success=notification.success,
        )

        try:
            if notification.success:
                success_text = (
                    f"✅ <b>Успешная оплата!</b> ✨\n\n"
                    f"Следующее списание через {notification.tariff_duration_days} дней — {notification.tariff_price}\n\n"
                    f"Каналов добавлено: {notification.channels_count}/{notification.channels_limit}"
                )

                keyboard = add_channels_with_menu_kb()
                file_name = "payment_success.jpg"
            else:
                error_text = (
                    notification.error_message
                    if notification.error_message
                    else "❌ Произошла ошибка, попробуйте снова"
                )
                success_text = error_text
                keyboard = payment_kb()
                file_name = "failed_payment.jpg"

            # Создаем реальный объект Message от aiogram с данными пользователя из БД
            message = await create_message_from_notification(
                notification, self.bot
            )

            if message:
                # Используем send_file_message для отправки/редактирования
                edit_message = bool(
                    notification.message_id and notification.chat_id
                )

                logger.debug(
                    "Используется send_file_message",
                    user_id=notification.user_id,
                    payment_id=notification.payment_id,
                    edit_message=edit_message,
                    message_id=notification.message_id,
                )

                await send_file_message(
                    message=message,
                    file_name=file_name,
                    caption=success_text,
                    keyboard=keyboard,
                    bot=self.bot,
                    edit_message=edit_message,
                    parse_mode=ParseMode.HTML,
                )
            else:
                logger.debug(
                    "Не удалось создать message, используем прямой вызов",
                    user_id=notification.user_id,
                    payment_id=notification.payment_id,
                )

                try:
                    mediafiles_dir: Path = (
                        settings.BASE_DIR / MEDIA_FILES_PATH
                    ).resolve()
                    file_path: Path = (mediafiles_dir / file_name).resolve()

                    if file_path.exists():
                        logger.debug(
                            "Отправка файла через send_file с кешем",
                            file_name=file_name,
                            user_id=notification.user_id,
                        )

                        redis_key = f"image:{file_name}"
                        await send_file(
                            bot=self.bot,
                            file_path=str(file_path),
                            redis_key=redis_key,
                            user_tg_id=notification.user_id,
                            caption=success_text,
                            reply_markup=keyboard,
                            parse_mode=ParseMode.HTML,
                        )

                        # Удаляем исходное сообщение, если оно есть
                        if notification.message_id and notification.chat_id:
                            try:
                                await self.bot.delete_message(
                                    chat_id=notification.chat_id,
                                    message_id=notification.message_id,
                                )
                                logger.debug(
                                    "Исходное сообщение удалено",
                                    message_id=notification.message_id,
                                )
                            except TelegramBadRequest:
                                pass
                    else:
                        if notification.message_id and notification.chat_id:
                            try:
                                await self.bot.delete_message(
                                    chat_id=notification.chat_id,
                                    message_id=notification.message_id,
                                )
                                logger.debug(
                                    "Исходное сообщение удалено (файл не найден)"
                                )
                            except TelegramBadRequest:
                                pass
                        await self.bot.send_message(
                            chat_id=notification.user_id,
                            text=success_text,
                            reply_markup=keyboard,
                            parse_mode="HTML",
                        )

                        # Удаляем исходное сообщение, если оно есть
                        if notification.message_id and notification.chat_id:
                            try:
                                await self.bot.delete_message(
                                    chat_id=notification.chat_id,
                                    message_id=notification.message_id,
                                )
                                logger.debug(
                                    "Исходное сообщение удалено (файл не найден)"
                                )
                            except TelegramBadRequest:
                                pass
                except Exception as e:
                    logger.error(
                        "Ошибка при отправке файла",
                        file_name=file_name,
                        user_id=notification.user_id,
                        payment_id=notification.payment_id,
                        error=str(e),
                    )
                    # Fallback на обычное сообщение
                    logger.debug(
                        "Fallback: отправка текстового сообщения",
                        user_id=notification.user_id,
                    )

                    await self.bot.send_message(
                        chat_id=notification.user_id,
                        text=success_text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                    # Удаляем исходное сообщение, если оно есть
                    if notification.message_id and notification.chat_id:
                        try:
                            await self.bot.delete_message(
                                chat_id=notification.chat_id,
                                message_id=notification.message_id,
                            )
                            logger.debug(
                                "Исходное сообщение удалено (fallback)"
                            )
                        except TelegramBadRequest:
                            pass

            logger.info(
                "Уведомление о платеже успешно отправлено",
                user_id=notification.user_id,
                payment_id=notification.payment_id,
                success=notification.success,
            )
        except Exception as e:
            logger.error(
                "Ошибка при обработке уведомления о платеже",
                error=str(e),
                user_id=notification.user_id,
                payment_id=notification.payment_id,
                exc_info=True,
            )
