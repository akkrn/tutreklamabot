import asyncio

import structlog
from django.core.management.base import BaseCommand

from userbot.userbot_daemon import UserbotDaemon

logger = structlog.getLogger(__name__)


class Command(BaseCommand):
    help = "Запускает userbot daemon для прослушивания каналов"

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-loop",
            action="store_true",
            help="Запустить без бесконечного цикла (для тестов)",
        )

    def handle(self, *args, **options):
        """Запуск userbot daemon"""
        logger.info("Запуск userbot daemon...")

        try:
            if options["no_loop"]:
                asyncio.run(self._test_connection())
            else:
                asyncio.run(self._run_daemon())
        except KeyboardInterrupt:
            logger.warning("Получен сигнал остановки. Завершение работы...")
        except Exception as e:
            logger.error(
                "Ошибка при запуске userbot", error=str(e), exc_info=True
            )

    async def _run_daemon(self):
        """Запускает daemon в обычном режиме"""
        daemon = UserbotDaemon()
        await daemon.start()

    async def _test_connection(self):
        """Тестирует соединение userbot"""
        try:
            from django.conf import settings
            from telethon import TelegramClient

            client = TelegramClient(
                settings.USERBOT_SESSION_NAME,
                settings.TELEGRAM_API_ID,
                settings.TELEGRAM_API_HASH,
            )  # TODO Как работает полинг, органичить кол-во запросв, в идеале юзать вебхуки

            await client.connect()

            if await client.is_user_authorized():
                me = await client.get_me()
                logger.info(
                    "Userbot подключен успешно",
                    username=me.username,
                    first_name=me.first_name,
                )
            else:
                logger.error("Userbot не авторизован")

            await client.disconnect()

        except Exception as e:
            logger.error(
                "Ошибка подключения userbot", error=str(e), exc_info=True
            )
