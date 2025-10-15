from typing import Dict, Optional

import structlog

from bot.models import ChannelSubscription, UserBot
from userbot.userbot_manager import userbot_manager

logger = structlog.getLogger(__name__)


class UserbotBalancer:
    """Балансировщик нагрузки между юзерботами"""

    def __init__(self):
        self.userbot_loads: Dict[int, int] = {}  #

    async def get_best_userbot(self) -> Optional[UserBot]:
        """Возвращает лучший юзербот для подписки на новый канал"""
        active_userbots = await UserBot.objects.filter(
            status=UserBot.STATUS_ACTIVE, is_active=True
        ).aiterator()

        best_userbot = None
        min_load = float("inf")

        async for userbot in active_userbots:
            if not userbot.can_subscribe_more:
                continue

            # Получаем текущую нагрузку из БД (количество подписок)
            load = await self._get_userbot_load_from_db(userbot)

            if load < min_load:
                min_load = load
                best_userbot = userbot

        return best_userbot

    async def _get_userbot_load_from_db(self, userbot: UserBot) -> int:
        """Получает текущую нагрузку юзербота из БД"""
        return await userbot.channel_subscriptions.filter(
            is_subscribed=True
        ).acount()


class UserbotPoolManager:
    """Менеджер пула юзерботов с автоматическим управлением"""

    def __init__(self):
        self.balancer = UserbotBalancer()
        self.min_userbots = 1
        self.max_userbots = 5

    async def start(self):
        """Запускает менеджер пула"""
        logger.info("UserbotPoolManager запущен")

        # Проверяем необходимость добавления юзерботов
        await self._check_and_add_userbots()

    async def _check_and_add_userbots(self):
        """Проверяет необходимость добавления новых юзерботов"""
        active_count = await UserBot.objects.filter(
            status=UserBot.STATUS_ACTIVE, is_active=True
        ).acount()

        if active_count < self.min_userbots:
            logger.warning(
                f"Недостаточно активных юзерботов: {active_count}/{self.min_userbots}"
            )
            # Здесь можно добавить логику создания новых юзерботов

    async def get_userbot_for_channel(
        self, channel_link: str
    ) -> Optional[UserBot]:
        """Возвращает лучший юзербот для подписки на канал"""
        return await self.balancer.get_best_userbot()

    async def handle_userbot_ban(self, banned_userbot: UserBot):
        """Обрабатывает бан юзербота - переподписывает его каналы на других юзерботов"""
        logger.warning(
            f"Юзербот {banned_userbot.name} забанен, мигрируем каналы"
        )

        # Получаем все каналы забаненного юзербота
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

        # Находим активного юзербота для миграции
        target_userbot = await self.balancer.get_best_userbot()
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
                # Обновляем подписку в БД
                subscription.userbot = target_userbot
                await subscription.asave()

                # Переподписываемся в Telegram через нового юзербота
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
            # Получаем клиент юзербота из менеджера
            client = userbot_manager.active_userbots.get(userbot.id)
            if not client:
                logger.error(f"Клиент юзербота {userbot.name} не найден")
                return False

            # Формируем ссылку на канал
            if channel.main_username:
                channel_link = f"https://t.me/{channel.main_username}"
            elif channel.link_subscription:
                channel_link = channel.link_subscription
            else:
                logger.error(f"Нет ссылки для канала {channel.title}")
                return False

            # Подписываемся на канал
            await client.join_channel(channel_link)
            logger.info(
                f"Успешно подписались на {channel.title} через {userbot.name}"
            )
            return True

        except Exception as e:
            logger.error(f"Ошибка подписки на канал {channel.title}: {e}")
            return False


# Глобальные экземпляры
userbot_balancer = UserbotBalancer()
userbot_pool_manager = UserbotPoolManager()
