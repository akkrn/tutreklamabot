import asyncio
from typing import Dict
from typing import Optional

import structlog
from telethon import TelegramClient
from telethon import events
from telethon.errors import AuthKeyUnregisteredError
from telethon.errors import SessionRevokedError

from bot.models import UserBot
from userbot.event_manager import EventType
from userbot.event_manager import event_manager
from userbot.redis_messages import NewAdMessage

logger = structlog.getLogger(__name__)


class UserbotManager:
    """Менеджер для управления юзерботами с автоматическим восстановлением"""

    def __init__(self):
        self.active_userbots: Dict[int, TelegramClient] = {}
        self.userbot_tasks: Dict[int, asyncio.Task] = {}
        self.running = False

    async def start(self):
        """Запускает менеджер юзерботов"""
        self.running = True
        logger.info("UserbotManager запущен")

        # Загружаем активные юзерботы
        await self._load_active_userbots()

        # Запускаем мониторинг
        asyncio.create_task(self._monitor_userbots())

    async def stop(self):
        """Останавливает менеджер юзерботов"""
        self.running = False

        # Останавливаем все задачи
        for task in self.userbot_tasks.values():
            task.cancel()

        # Отключаем всех клиентов
        for client in self.active_userbots.values():
            if client.is_connected():
                await client.disconnect()

        self.active_userbots.clear()
        self.userbot_tasks.clear()
        logger.info("UserbotManager остановлен")

    async def _load_active_userbots(self):
        """Загружает активные юзерботы из базы данных"""
        active_userbots = await UserBot.objects.filter(
            status=UserBot.STATUS_ACTIVE, is_active=True
        ).aiterator()

        async for userbot in active_userbots:
            await self._start_userbot(userbot)

    async def _start_userbot(self, userbot: UserBot):
        """Запускает конкретный юзербот"""
        try:
            client = await self._create_client(userbot)
            if not client:
                return

            # Регистрируем обработчики
            client.add_event_handler(
                self._create_message_handler(userbot),
                events.NewMessage(incoming=True),
            )

            # Сохраняем клиент
            self.active_userbots[userbot.id] = client

            # Запускаем задачу
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

    def _create_message_handler(self, userbot: UserBot):
        """Создает обработчик сообщений для юзербота"""

        async def handler(event):
            await self._handle_new_message(event, userbot)

        return handler

    async def _handle_new_message(self, event, userbot: UserBot):
        """Обрабатывает новое сообщение"""
        try:
            message = event.message
            channel = await self._get_channel_by_telegram_id(
                message.peer_id.channel_id
            )

            if not channel:
                return

            # Проверяем, является ли сообщение рекламой
            if not await self._is_ad_message(message):
                return

            # Создаем новость
            from bot.models import ChannelNews

            await ChannelNews.objects.acreate(
                channel=channel,
                message_id=message.id,
                message=message.text or "",
            )

            # Отправляем уведомление
            ad_message = NewAdMessage(
                channel_id=channel.telegram_id,
                channel_title=channel.title,
                message_id=message.id,
                message_text=message.text or "",
                channel_link=f"https://t.me/{channel.main_username}"
                if channel.main_username
                else channel.link_subscription or "",
            )

            await event_manager.publish_event(
                EventType.NEW_AD_MESSAGE, ad_message, "bot:new_ad"
            )

            logger.debug(f"Обработано рекламное сообщение из {channel.title}")

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def _run_userbot(self, userbot: UserBot, client: TelegramClient):
        """Запускает юзербот и обрабатывает события"""
        try:
            await client.run_until_disconnected()
        except (AuthKeyUnregisteredError, SessionRevokedError) as e:
            logger.warning(f"Сессия юзербота {userbot.name} отозвана: {e}")
            await self._handle_session_error(userbot, str(e))
        except Exception as e:
            logger.error(f"Ошибка в юзерботе {userbot.name}: {e}")
            await self._handle_userbot_error(userbot, str(e))

    async def _monitor_userbots(self):
        """Мониторит состояние юзерботов"""
        while self.running:
            try:
                await asyncio.sleep(30)  # Проверяем каждые 30 секунд

                for userbot_id, task in list(self.userbot_tasks.items()):
                    if task.done():
                        # Задача завершилась, перезапускаем юзербот
                        logger.info(f"Перезапускаем юзербот {userbot_id}")
                        await self._restart_userbot(userbot_id)

            except Exception as e:
                logger.error(f"Ошибка мониторинга юзерботов: {e}")

    async def _restart_userbot(self, userbot_id: int):
        """Перезапускает юзербот"""
        try:
            # Удаляем старые задачи и клиенты
            if userbot_id in self.userbot_tasks:
                self.userbot_tasks[userbot_id].cancel()
                del self.userbot_tasks[userbot_id]

            if userbot_id in self.active_userbots:
                client = self.active_userbots[userbot_id]
                if client.is_connected():
                    await client.disconnect()
                del self.active_userbots[userbot_id]

            # Получаем юзербот из БД
            try:
                userbot = await UserBot.objects.aget(id=userbot_id)
            except UserBot.DoesNotExist:
                logger.warning(f"Юзербот {userbot_id} не найден в БД")
                return

            # Перезапускаем
            await self._start_userbot(userbot)

        except Exception as e:
            logger.error(f"Ошибка перезапуска юзербота {userbot_id}: {e}")

    async def _handle_session_error(self, userbot: UserBot, error: str):
        """Обрабатывает ошибки сессии (бан или отзыв)"""
        logger.warning(f"Сессия юзербота {userbot.name} отозвана: {error}")

        # Проверяем, это бан или просто отзыв сессии
        if "AUTH_KEY_UNREGISTERED" in str(error) or "SESSION_REVOKED" in str(
            error
        ):
            # Это бан - мигрируем каналы
            from userbot.userbot_pool import userbot_pool_manager

            await userbot_pool_manager.handle_userbot_ban(userbot)
        else:
            # Просто ошибка сессии - помечаем как ошибку
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

    async def _get_channel_by_telegram_id(self, telegram_id: int):
        """Получает канал по telegram_id"""
        from bot.models import Channel

        try:
            return await Channel.objects.aget(telegram_id=telegram_id)
        except Channel.DoesNotExist:
            return None

    async def _is_ad_message(self, message) -> bool:
        """Проверяет, является ли сообщение рекламой"""
        # Простая проверка по ключевым словам
        text = (message.text or "").lower()
        ad_keywords = [
            "реклама",
            "реклам",
            "рекламн",
            "рекламное",
            "рекламный",
            "рекламная",
        ]
        return any(keyword in text for keyword in ad_keywords)

    async def add_userbot(self, userbot: UserBot):
        """Добавляет новый юзербот"""
        await self._start_userbot(userbot)

    async def remove_userbot(self, userbot_id: int):
        """Удаляет юзербот"""
        if userbot_id in self.userbot_tasks:
            self.userbot_tasks[userbot_id].cancel()
            del self.userbot_tasks[userbot_id]

        if userbot_id in self.active_userbots:
            client = self.active_userbots[userbot_id]
            if client.is_connected():
                await client.disconnect()
            del self.active_userbots[userbot_id]


# Глобальный экземпляр менеджера
userbot_manager = UserbotManager()
