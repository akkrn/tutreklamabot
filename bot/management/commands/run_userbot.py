import asyncio
import sys
import time

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

        start_time = time.time()
        # 24 часа = 86400 секунд
        restart_interval = 86400

        logger.info(
            f"Userbot manager запущен. Автоматическая перезагрузка через {restart_interval/3600:.0f} часов"
        )

        # Запускаем задачу для автоматической перезагрузки
        restart_task = asyncio.create_task(
            self._scheduled_restart(restart_interval)
        )

        # Ждем бесконечно или до перезагрузки
        try:
            while True:
                await asyncio.sleep(100)

                # Проверяем время работы (на случай если restart_task не сработает)
                uptime = time.time() - start_time
                if uptime >= restart_interval:
                    logger.info(
                        f"Достигнут лимит времени работы ({uptime/3600:.1f} часов), "
                        f"перезагружаем контейнер"
                    )
                    restart_task.cancel()
                    await userbot_manager.stop()
                    sys.exit(0)

        except KeyboardInterrupt:
            restart_task.cancel()
            await userbot_manager.stop()

    async def _scheduled_restart(self, interval: int):
        """Запланированная перезагрузка через заданный интервал"""
        try:
            await asyncio.sleep(interval)
            logger.info(
                f"Сработал таймер перезагрузки ({interval/3600:.0f} часов), "
                f"перезагружаем контейнер"
            )
            await userbot_manager.stop()
            sys.exit(0)
        except asyncio.CancelledError:
            pass

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
