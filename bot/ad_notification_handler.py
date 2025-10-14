from typing import Optional

import redis.asyncio as redis
import structlog
from django.conf import settings

from bot.keyboards import back_to_menu_kb
from bot.models import Channel
from bot.tools import send_long
from userbot.redis_messages import NewAdMessage
from userbot.redis_messages import deserialize_message

logger = structlog.getLogger(__name__)


class AdNotificationHandler:
    """Обработчик уведомлений о новых рекламных постах"""

    def __init__(self, bot):
        self.bot = bot
        self.redis_client: Optional[redis.Redis] = None
        self.pubsub: Optional[redis.client.PubSub] = None

    async def connect(self):
        """Подключается к Redis"""
        try:
            self.redis_client = redis.Redis(
                host=getattr(settings, "BOT_REDIS_HOST", "localhost"),
                port=getattr(settings, "BOT_REDIS_PORT", 6379),
                db=getattr(settings, "BOT_REDIS_DB", 0),
                decode_responses=True,
            )
            await self.redis_client.ping()
            logger.info("AdNotificationHandler подключен к Redis")
        except Exception as e:
            logger.error(
                f"Ошибка подключения AdNotificationHandler к Redis: {e}"
            )
            raise

    async def disconnect(self):
        """Отключается от Redis"""
        if self.pubsub:
            await self.pubsub.close()
        if self.redis_client:
            await self.redis_client.close()

    async def listen_for_ad_notifications(self):
        """Слушает уведомления о новых рекламных постах"""
        channel = "bot:new_ad"

        try:
            self.pubsub = self.redis_client.pubsub()
            await self.pubsub.subscribe(channel)

            logger.info(f"Слушаем канал {channel} для уведомлений о рекламе")

            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    ad_message = deserialize_message(
                        message["data"], NewAdMessage
                    )
                    if ad_message:
                        await self.handle_new_ad(ad_message)

        except Exception as e:
            logger.error(f"Ошибка прослушивания уведомлений о рекламе: {e}")

    async def handle_new_ad(self, ad_message: NewAdMessage):
        """Обрабатывает уведомление о новом рекламном посте"""
        try:
            try:
                channel = await Channel.objects.aget(
                    telegram_id=ad_message.channel_id
                )
            except Channel.DoesNotExist:
                logger.warning(
                    f"Канал с ID {ad_message.channel_id} не найден в БД"
                )
                return

            users = []
            async for user in channel.users.all():
                users.append(user)

            if not users:
                logger.info(f"Нет подписчиков на канал {channel.title}")
                return

            channel_link = (
                ad_message.channel_link
                or f"https://t.me/{channel.main_username}"
            )

            # Безопасно экранируем текст сообщения
            safe_message_text = ad_message.message_text
            safe_channel_title = ad_message.channel_title

            message_text = f"📢 Новый рекламный пост в канале [{safe_channel_title}]({channel_link})\n\n"
            message_text += f"{safe_message_text}\n\n"

            sent_count = 0
            for user in users:
                try:
                    await send_long(
                        self.bot,
                        user.tg_chat_id,
                        message_text,
                        reply_markup=back_to_menu_kb(),
                    )
                    sent_count += 1
                except Exception as e:
                    logger.error(
                        f"Ошибка отправки уведомления пользователю {user.tg_user_id}: {e}"
                    )
                # TODO добавить отправку только активным пользователям и отписку от каналов для него

            logger.info(
                f"Отправлено уведомлений о рекламе: {sent_count} из {len(users)} подписчиков канала {channel.title}"
            )

        except Exception as e:
            logger.error(
                f"Ошибка обработки уведомления о рекламе: {e}", exc_info=True
            )
