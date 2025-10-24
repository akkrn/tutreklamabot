import asyncio
from typing import Optional

import structlog
from asgiref.sync import sync_to_async
from telethon import TelegramClient
from telethon.errors import AuthKeyUnregisteredError, SessionRevokedError

from bot.models import ChannelSubscription, UserBot

logger = structlog.getLogger(__name__)


class UserbotCore:
    """Основной класс для управления жизненным циклом юзерботов"""

    def __init__(self):
        self.active_userbots: dict[int, TelegramClient] = {}
        self.userbot_tasks: dict[int, asyncio.Task] = {}
        self.running = False

    async def start(self):
        """Запускает менеджер юзерботов"""
        self.running = True
        logger.info("UserbotCore запущен")

        await self._load_active_userbots()
        asyncio.create_task(self._monitor_userbots())

    async def stop(self):
        """Останавливает менеджер юзерботов"""
        self.running = False

        for task in self.userbot_tasks.values():
            task.cancel()

        for client in self.active_userbots.values():
            if client.is_connected():
                await client.disconnect()

        self.active_userbots.clear()
        self.userbot_tasks.clear()
        logger.info("UserbotCore остановлен")

    async def _load_active_userbots(self):
        """Загружает активные юзерботы из базы данных"""

        def get_active_userbots():
            return list(
                UserBot.objects.filter(
                    status=UserBot.STATUS_ACTIVE, is_active=True
                )
            )

        active_userbots = await sync_to_async(get_active_userbots)()

        for userbot in active_userbots:
            await self._start_userbot(userbot)

    async def _start_userbot(self, userbot: UserBot):
        """Запускает конкретный юзербот"""
        try:
            client = await self._create_client(userbot)
            if not client:
                return

            self.active_userbots[userbot.id] = client
            task = asyncio.create_task(self._run_userbot(userbot, client))
            self.userbot_tasks[userbot.id] = task

            logger.info(f"Юзербот {userbot.name} запущен")

        except Exception as e:
            logger.error(f"Ошибка запуска юзербота {userbot.name}: {e}")
            userbot.status = UserBot.STATUS_ERROR
            userbot.last_error = str(e)
            await userbot.asave()

    async def _create_client(
        self, userbot: UserBot
    ) -> Optional[TelegramClient]:
        """Создает клиент для юзербота"""
        try:
            if userbot.string_session:
                client = TelegramClient(
                    userbot.string_session, userbot.api_id, userbot.api_hash
                )
            else:
                client = TelegramClient(
                    userbot.get_session_path(), userbot.api_id, userbot.api_hash
                )

            await client.connect()

            if not await client.is_user_authorized():
                logger.warning(f"Юзербот {userbot.name} не авторизован")
                await client.disconnect()
                return None

            return client

        except Exception as e:
            logger.error(f"Ошибка создания клиента для {userbot.name}: {e}")
            return None

    async def _run_userbot(self, userbot: UserBot, client: TelegramClient):
        """Запускает юзербот и обрабатывает события"""
        logger.info(f"Запускаем юзербот {userbot.name} (ID: {userbot.id})")
        try:
            logger.info(
                f"Юзербот {userbot.name} подключен и слушает сообщения..."
            )
            await client.run_until_disconnected()
            logger.error(f"Юзербот {userbot.name} отключился")
        except (AuthKeyUnregisteredError, SessionRevokedError) as e:
            logger.error(f"Сессия юзербота {userbot.name} отозвана: {e}")
            await self._handle_session_error(userbot, str(e))
        except Exception as e:
            logger.exception(f"Ошибка в юзерботе {userbot.name}: {e}")
            await self._handle_userbot_error(userbot, str(e))

    async def _monitor_userbots(self):
        """Мониторит состояние юзерботов"""
        while self.running:
            try:
                await asyncio.sleep(30)

                for userbot_id, task in list(self.userbot_tasks.items()):
                    if task.done():
                        logger.info(f"Перезапускаем юзербот {userbot_id}")
                        await self._restart_userbot(userbot_id)

            except Exception as e:
                logger.error(f"Ошибка мониторинга юзерботов: {e}")

    async def _restart_userbot(self, userbot_id: int):
        """Перезапускает юзербот"""
        try:

            def get_userbot():
                return UserBot.objects.get(id=userbot_id)

            userbot = await sync_to_async(get_userbot)()

            if userbot_id in self.active_userbots:
                del self.active_userbots[userbot_id]
            if userbot_id in self.userbot_tasks:
                del self.userbot_tasks[userbot_id]

            await self._start_userbot(userbot)

        except UserBot.DoesNotExist:
            logger.error(f"Юзербот {userbot_id} не найден в БД")

    async def _handle_session_error(self, userbot: UserBot, error: str):
        """Обрабатывает ошибки сессии (бан или отзыв)"""
        logger.warning(f"Сессия юзербота {userbot.name} отозвана: {error}")

        # Проверяем, это бан или просто отзыв сессии
        if "AUTH_KEY_UNREGISTERED" in str(error) or "SESSION_REVOKED" in str(
            error
        ):
            # Это бан - помечаем как забаненный
            userbot.status = UserBot.STATUS_ERROR
            userbot.is_active = False
            userbot.last_error = f"Забанен: {error}"
            await userbot.asave()

            # Уведомляем о бане для миграции каналов
            # Импортируем здесь, чтобы избежать циклического импорта
            import userbot.userbot_manager

            await userbot.userbot_manager.userbot_manager.handle_userbot_ban(
                userbot
            )
        else:
            userbot.status = UserBot.STATUS_ERROR
            userbot.last_error = f"Ошибка сессии: {error}"
            await userbot.asave()

        # Удаляем из активных
        if userbot.id in self.active_userbots:
            del self.active_userbots[userbot.id]
        if userbot.id in self.userbot_tasks:
            del self.userbot_tasks[userbot.id]

    async def _handle_userbot_error(self, userbot: UserBot, error: str):
        """Обрабатывает ошибки юзербота"""
        userbot.status = UserBot.STATUS_ERROR
        userbot.last_error = error
        await userbot.asave()

        # Планируем перезапуск через 5 минут
        asyncio.create_task(self._delayed_restart(userbot.id, 300))

    async def _delayed_restart(self, userbot_id: int, delay: int):
        """Перезапускает юзербот с задержкой"""
        await asyncio.sleep(delay)
        await self._restart_userbot(userbot_id)

    async def _select_best_userbot(self) -> Optional[UserBot]:
        """Выбирает лучший юзербот для подписки на канал"""

        # Получаем всех активных юзерботов
        def get_active_userbots():
            return list(
                UserBot.objects.filter(
                    status=UserBot.STATUS_ACTIVE, is_active=True
                )
            )

        active_userbots = await sync_to_async(get_active_userbots)()

        if not active_userbots:
            return None

        best_userbot = None
        min_subscriptions = float("inf")

        for userbot in active_userbots:

            def count_subscriptions():
                return ChannelSubscription.objects.filter(
                    userbot=userbot, is_subscribed=True
                ).count()

            subscription_count = await sync_to_async(count_subscriptions)()

            if subscription_count < min_subscriptions:
                min_subscriptions = subscription_count
                best_userbot = userbot

        return best_userbot

    def get_client(self, userbot_id: int) -> Optional[TelegramClient]:
        """Получает клиент юзербота по ID"""
        return self.active_userbots.get(userbot_id)
