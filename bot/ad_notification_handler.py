from typing import Optional

import redis.asyncio as redis
import structlog
from django.conf import settings

from bot.keyboards import new_menu_kb
from bot.models import Channel
from bot.tools import send_long, truncate_text
from userbot.redis_messages import NewAdMessage, deserialize_message

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
            safe_message_text = truncate_text(ad_message.message_text)
            safe_channel_title = ad_message.channel_title

            # Определяем тип канала и формируем соответствующее сообщение
            if channel.is_private:
                channel_type_text = "Реклама из закрытого канала"
                button_text = "Перейти в канал"
                # Для закрытого канала ссылка ведет на канал
                action_link = channel_link
            else:
                channel_type_text = "Реклама из открытого канала"
                button_text = "Перейти к посту"
                action_link = f"{channel_link}/{ad_message.message_id}"

            message_text = f"{channel_type_text}: [{safe_channel_title}]({channel_link})\n\n"
            message_text += f"{safe_message_text}\n\n"
            message_text += f"[{button_text} →]({action_link})"

            sent_count = 0
            failed_count = 0

            for user in users:
                try:
                    logger.debug(
                        f"Отправляем уведомление пользователю {user.tg_user_id} (chat_id: {user.tg_chat_id})"
                    )

                    await send_long(
                        self.bot,
                        user.tg_chat_id,
                        message_text,
                        reply_markup=new_menu_kb(),
                    )
                    sent_count += 1
                    logger.debug(
                        f"Уведомление успешно отправлено пользователю {user.tg_user_id}"
                    )

                except Exception as e:
                    failed_count += 1
                    error_type = type(e).__name__

                    # Более детальное логирование в зависимости от типа ошибки
                    if "timeout" in str(e).lower():
                        logger.warning(
                            f"Таймаут при отправке уведомления пользователю {user.tg_user_id} (chat_id: {user.tg_chat_id}): {e}"
                        )
                    elif (
                        "blocked" in str(e).lower()
                        or "bot was blocked" in str(e).lower()
                    ):
                        logger.warning(
                            f"Пользователь {user.tg_user_id} заблокировал бота: {e}"
                        )
                    elif "chat not found" in str(e).lower():
                        logger.warning(
                            f"Чат пользователя {user.tg_user_id} не найден: {e}"
                        )
                    else:
                        logger.error(
                            f"Ошибка отправки уведомления пользователю {user.tg_user_id} (chat_id: {user.tg_chat_id}): {error_type}: {e}"
                        )

                    # TODO добавить отправку только активным пользователям и отписку от каналов для него

            logger.info(
                f"Отправлено уведомлений о рекламе: {sent_count} из {len(users)} подписчиков канала {channel.title} "
                f"(неудачных: {failed_count})"
            )

        except Exception as e:
            logger.error(
                f"Ошибка обработки уведомления о рекламе: {e}", exc_info=True
            )
