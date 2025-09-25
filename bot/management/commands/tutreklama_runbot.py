import asyncio

from aiogram import Dispatcher, Bot
from django.core.management.base import BaseCommand
import structlog


from bot import bot_texts
from bot.bot import build_bot


logger = structlog.getLogger(__name__)


class Command(BaseCommand):
    help = "Запустить бота tutreklama"

    def handle(self, *args, **options):
        bot_texts.TextsStore.initialize()
        logger.info("Инициализировали кеш текстов")
        asyncio.run(self.run_bot())

    async def run_bot(self):
        bot_app, bot_dp = await build_bot()
        await self.async_run_polling(bot_app, bot_dp)
        logger.info("All bots shut down.")

    async def async_run_polling(
        self,
        tg_bot: Bot,
        dp: Dispatcher,
    ):
        try:
            logger.info("Запускаем бота...")
            await dp.start_polling(tg_bot)

        finally:
            logger.info("Бот завершил работу.")
