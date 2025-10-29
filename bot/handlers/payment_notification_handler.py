"""Обработчик уведомлений о платежах из Redis"""

import structlog
from aiogram import Bot

from bot.handlers.helpers import send_file_message
from bot.keyboards import add_channels_with_menu_kb, payment_kb
from userbot.redis_messages import PaymentNotificationMessage

logger = structlog.getLogger(__name__)


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
                file_name = "payment_failed.jpg"

            # Если есть message_id и chat_id, редактируем сообщение
            if notification.message_id and notification.chat_id:
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

                    fake_message = FakeMessage(
                        self.bot,
                        notification.chat_id,
                        notification.message_id,
                    )

                    await send_file_message(
                        message=fake_message,
                        file_name=file_name,
                        caption=success_text,
                        keyboard=keyboard,
                        bot=self.bot,
                        edit_message=True,
                    )
                except Exception as e:
                    logger.error(
                        "Ошибка при редактировании сообщения",
                        error=str(e),
                        chat_id=notification.chat_id,
                        message_id=notification.message_id,
                    )
                    # Если не удалось отредактировать, отправляем новое сообщение
                    await self.bot.send_message(
                        chat_id=notification.user_id,
                        text=success_text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
            else:
                # Отправляем новое сообщение, если нет данных для редактирования
                await self.bot.send_message(
                    chat_id=notification.user_id,
                    text=success_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

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
