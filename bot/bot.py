from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from aiogram.types import BotCommandScopeDefault, BotCommand

from django.conf import settings
import redis.asyncio as redis
import structlog

from bot.middlewares import (
    CurrentUserMiddleware,
    IgnoreMessageNotModifiedMiddleware,
)
from bot.handlers import (
    command_handlers,
    status_handlers,
    other_handlers,
)

logger = structlog.getLogger(__name__)

COMMANDS_RU = [
    BotCommand(command="start", description="Перезапустить бота"),
]

COMMANDS_EN = [
    BotCommand(command="start", description="Restart the bot"),
]


async def on_startup(bot: Bot):
    await bot.delete_my_commands(scope=BotCommandScopeDefault())

    await bot.set_my_commands(
        COMMANDS_RU, scope=BotCommandScopeDefault(), language_code="ru"
    )
    await bot.set_my_commands(COMMANDS_EN, scope=BotCommandScopeDefault())


async def on_shutdown(bot: Bot):
    logger.info("Завершение работы бота...")
    # await bot.session.close()
    # await redis.Redis(db=1).close()
    # logger.info("Redis connection closed")


async def build_bot() -> tuple[Bot, Dispatcher]:
    token = settings.BOT_TOKEN
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))

    redis_client = redis.Redis(
        db=settings.BOT_REDIS_DB,
        host=settings.BOT_REDIS_HOST,
        port=settings.BOT_REDIS_PORT,
    )

    storage = RedisStorage(redis_client)

    dp = Dispatcher(storage=storage)

    dp.include_router(command_handlers.router)
    dp.include_router(status_handlers.router)

    # Всегда последний, так как там пустой приемщик
    dp.include_router(other_handlers.router)

    dp.update.middleware(CurrentUserMiddleware())
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.callback_query.middleware(IgnoreMessageNotModifiedMiddleware())


    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("Сформировали приложение")

    return bot, dp
