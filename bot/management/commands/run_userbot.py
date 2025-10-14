import asyncio

import structlog
from django.core.management.base import BaseCommand

from userbot.userbot_manager import userbot_manager

logger = structlog.getLogger(__name__)


class Command(BaseCommand):
    help = "Запускает userbot manager для управления юзерботами"

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-loop",
            action="store_true",
            help="Запустить без бесконечного цикла (для тестов)",
        )

    def handle(self, *args, **options):
        """Запуск userbot manager"""
        logger.info("Запуск userbot manager...")

        try:
            if options["no_loop"]:
                asyncio.run(self._test_connection())
            else:
                asyncio.run(self._run_manager())
        except KeyboardInterrupt:
            logger.warning("Получен сигнал остановки. Завершение работы...")
        except Exception as e:
            logger.error(
                "Ошибка при запуске userbot manager",
                error=str(e),
                exc_info=True,
            )

    async def _run_manager(self):
        """Запускает manager в обычном режиме"""
        await userbot_manager.start()

        # Ждем бесконечно
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await userbot_manager.stop()

    async def _test_connection(self):
        """Тестирует соединение с базой данных"""
        try:
            from bot.models import UserBot

            # Проверяем, есть ли активные юзерботы
            active_count = await UserBot.objects.filter(
                status=UserBot.STATUS_ACTIVE, is_active=True
            ).acount()

            logger.info(f"Найдено активных юзерботов: {active_count}")

            if active_count > 0:
                logger.info("Userbot manager готов к работе")
            else:
                logger.warning(
                    "Нет активных юзерботов. Создайте юзербот в Django Admin"
                )

        except Exception as e:
            logger.error(
                "Ошибка подключения к базе данных", error=str(e), exc_info=True
            )
