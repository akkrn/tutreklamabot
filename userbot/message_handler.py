from typing import TYPE_CHECKING

import structlog

from bot.models import Channel, ChannelNews
from core.event_manager import EventType, event_manager
from userbot.redis_messages import NewAdMessage
from utils.advertisement_detector import is_advertisement

if TYPE_CHECKING:
    from userbot.core import UserbotCore

logger = structlog.getLogger(__name__)


class MessageHandler:
    """Обработчик входящих сообщений от юзерботов"""

    def __init__(self, userbot_core: "UserbotCore"):
        self.userbot_core = userbot_core

    def create_message_handler(self, userbot):
        """Создает обработчик сообщений для конкретного юзербота"""

        async def message_handler(event):
            """Обработчик входящих сообщений"""
            try:
                if not hasattr(event, "message") or not hasattr(
                    event, "get_chat"
                ):
                    return

                message = event.message
                chat = await event.get_chat()

                if not hasattr(chat, "id") or chat.id is None:
                    return

                if not self._is_ad_message(message):
                    return

                channel = await self._get_channel_by_telegram_id(abs(chat.id))
                if not channel:
                    return

                await self._save_channel_news(channel, message)
                await self._send_ad_notification(channel, message)

            except Exception as e:
                logger.error(f"Ошибка обработки сообщения: {e}")

        return message_handler

    async def _get_channel_by_telegram_id(self, telegram_id: int):
        """Получает канал по telegram_id"""
        try:
            return await Channel.objects.aget(telegram_id=telegram_id)
        except Channel.DoesNotExist:
            return None

    def _is_ad_message(self, message) -> bool:
        """Проверяет, является ли сообщение рекламой"""
        return is_advertisement(message.text)

    async def _save_channel_news(self, channel: Channel, message):
        """Сохраняет новость в БД"""
        try:
            await ChannelNews.objects.acreate(
                channel=channel,
                message_id=message.id,
                message=message.text or "",
                created_at=message.date,
            )
            logger.info(f"Сохранена новость из канала {channel.title}")
        except Exception as e:
            logger.error(f"Ошибка сохранения новости: {e}")

    async def _send_ad_notification(self, channel: Channel, message):
        """Отправляет уведомление о рекламе"""
        try:
            ad_message = NewAdMessage(
                channel_id=channel.telegram_id,
                channel_title=channel.title,
                channel_link=self._get_channel_link(channel),
                message_id=message.id,
                message_text=message.text or "",
            )

            await event_manager.publish_event(
                EventType.NEW_AD_MESSAGE, ad_message, "bot:new_ad"
            )

            logger.info(f"Отправлено уведомление о рекламе из {channel.title}")

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")

    def _get_channel_link(self, channel: Channel) -> str:
        """Получает ссылку на канал"""
        if channel.main_username:
            return f"https://t.me/{channel.main_username}"
        elif channel.link_subscription:
            return channel.link_subscription
        else:
            return f"https://t.me/c/{channel.telegram_id}"
