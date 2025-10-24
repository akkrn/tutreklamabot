import structlog
from asgiref.sync import sync_to_async
from telethon import events

from bot.models import UserBot
from core.event_manager import EventType, event_manager
from userbot.core import UserbotCore
from userbot.message_handler import MessageHandler
from userbot.migration_handler import MigrationHandler
from userbot.subscription_handler import SubscriptionHandler

logger = structlog.getLogger(__name__)


class UserbotManager:
    """Главный менеджер для координации всех компонентов юзерботов"""

    def __init__(self):
        # Основные компоненты
        self.core = UserbotCore()
        self.subscription_handler = SubscriptionHandler(self.core)
        self.migration_handler = MigrationHandler(
            self.core, self.subscription_handler
        )
        self.message_handler = MessageHandler(self.core)

    async def start(self):
        """Запускает все компоненты менеджера юзерботов"""
        logger.info("UserbotManager запущен")
        await self.core.start()

        # Регистрируем обработчики событий
        event_manager.register_handler(
            EventType.SUBSCRIBE_CHANNELS,
            self.subscription_handler.handle_subscribe_request,
            "userbot:subscribe",
        )

        # Запускаем прослушивание событий
        await event_manager.start_listening()

        # Регистрируем обработчики сообщений для всех активных юзерботов
        await self._register_message_handlers()

    async def stop(self):
        """Останавливает все компоненты"""
        logger.info("Остановка UserbotManager")
        await self.core.stop()

    async def _register_message_handlers(self):
        """Регистрирует обработчики сообщений для всех активных юзерботов"""
        for userbot_id, client in self.core.active_userbots.items():
            try:

                def get_userbot():
                    return UserBot.objects.get(id=userbot_id)

                userbot = await sync_to_async(get_userbot)()

                # Регистрируем обработчик сообщений
                handler = self.message_handler.create_message_handler(userbot)
                client.add_event_handler(
                    handler, events.NewMessage(incoming=True)
                )

                logger.info(
                    f"Зарегистрирован обработчик сообщений для {userbot.name}"
                )

            except Exception as e:
                logger.error(
                    f"Ошибка регистрации обработчика для юзербота {userbot_id}: {e}"
                )

    async def handle_subscribe_request(self, request):
        """Делегирует обработку подписок в subscription_handler"""
        await self.subscription_handler.handle_subscribe_request(request)

    async def handle_userbot_ban(self, banned_userbot):
        """Делегирует обработку бана в migration_handler"""
        await self.migration_handler.handle_userbot_ban(banned_userbot)


# Глобальный экземпляр менеджера
userbot_manager = UserbotManager()
