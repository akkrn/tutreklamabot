"""Обработчик миграции каналов при бане юзербота"""

from typing import TYPE_CHECKING

import structlog

from bot.models import ChannelSubscription, UserBot

if TYPE_CHECKING:
    from userbot.core import UserbotCore
    from userbot.subscription_handler import SubscriptionHandler

logger = structlog.getLogger(__name__)


class MigrationHandler:
    """Обработчик миграции каналов при бане юзербота"""

    def __init__(
        self,
        userbot_core: "UserbotCore",
        subscription_handler: "SubscriptionHandler",
    ):
        self.userbot_core = userbot_core
        self.subscription_handler = subscription_handler

    async def handle_userbot_ban(self, banned_userbot: UserBot):
        """Обрабатывает бан юзербота - переподписывает его каналы на других юзерботов"""
        logger.warning(
            f"Юзербот {banned_userbot.name} забанен, мигрируем каналы"
        )

        subscriptions = []
        async for subscription in ChannelSubscription.objects.filter(
            userbot=banned_userbot, is_subscribed=True
        ).select_related("channel"):
            subscriptions.append(subscription)

        if not subscriptions:
            logger.info(
                f"У забаненного юзербота {banned_userbot.name} нет каналов для миграции"
            )
            return

        target_userbot = await self.userbot_core._select_best_userbot()
        if not target_userbot:
            logger.error("Нет доступных юзерботов для миграции каналов")
            return

        logger.info(
            f"Мигрируем {len(subscriptions)} каналов с {banned_userbot.name} на {target_userbot.name}"
        )

        # Мигрируем каналы
        migrated_count = 0
        for subscription in subscriptions:
            try:
                subscription.userbot = target_userbot
                await subscription.asave()

                success = await self._resubscribe_channel_in_telegram(
                    target_userbot, subscription.channel
                )

                if success:
                    migrated_count += 1
                    logger.info(
                        f"Успешно мигрирован канал {subscription.channel.title}"
                    )
                else:
                    logger.warning(
                        f"Не удалось переподписаться на канал {subscription.channel.title}"
                    )
                    # Откатываем изменения в БД
                    subscription.userbot = banned_userbot
                    await subscription.asave()

            except Exception as e:
                logger.error(
                    f"Ошибка миграции канала {subscription.channel.title}: {e}"
                )

        logger.info(
            f"Мигрировано {migrated_count} из {len(subscriptions)} каналов"
        )

        # Помечаем забаненный юзербот как неактивный
        banned_userbot.status = UserBot.STATUS_ERROR
        banned_userbot.is_active = False
        banned_userbot.last_error = "Забанен в Telegram"
        await banned_userbot.asave()

    async def _resubscribe_channel_in_telegram(
        self, userbot: UserBot, channel
    ) -> bool:
        """Переподписывается на канал в Telegram через указанного юзербота"""
        try:
            # Формируем ссылку на канал
            if channel.main_username:
                channel_link = f"https://t.me/{channel.main_username}"
            elif channel.link_subscription:
                channel_link = channel.link_subscription
            else:
                logger.error(f"Нет ссылки для канала {channel.title}")
                return False

            # Получаем клиент юзербота
            client = self.userbot_core.get_client(userbot.id)
            if not client:
                logger.error(f"Клиент юзербота {userbot.name} не найден")
                return False

            result = await self.subscription_handler._perform_subscription(
                client, channel_link
            )

            if result["success"]:
                logger.info(
                    f"Успешно переподписались на {channel.title} через {userbot.name}"
                )
                return True
            else:
                logger.error(
                    f"Ошибка переподписки на {channel.title}: {result['error_message']}"
                )
                return False

        except Exception as e:
            logger.error(f"Ошибка переподписки на канал {channel.title}: {e}")
            return False
