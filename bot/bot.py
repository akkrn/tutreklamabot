import redis.asyncio as redis
import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, BotCommandScopeDefault
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from aiohttp import ClientTimeout
from django.conf import settings

from bot.ad_notification_handler import AdNotificationHandler
from bot.handlers import (
    command_handlers,
    other_handlers,
    payment_handlers,
    status_handlers,
)
from bot.handlers.payment_notification_handler import (
    PaymentNotificationHandler,
)
from bot.middlewares import (
    CurrentUserMiddleware,
    IgnoreMessageNotModifiedMiddleware,
)
from core.event_manager import EventType, event_manager

logger = structlog.getLogger(__name__)

COMMANDS_RU = [
    BotCommand(command="menu", description="Главное меню"),
]


async def on_startup(bot: Bot):
    await bot.delete_my_commands(scope=BotCommandScopeDefault())

    await bot.set_my_commands(COMMANDS_RU, scope=BotCommandScopeDefault())

    global ad_handler
    global payment_notification_handler

    ad_handler = AdNotificationHandler(bot)
    payment_notification_handler = PaymentNotificationHandler(bot)

    event_manager.register_handler(
        EventType.NEW_AD_MESSAGE, ad_handler.handle_new_ad, "bot:new_ad"
    )

    event_manager.register_handler(
        EventType.PAYMENT_NOTIFICATION,
        payment_notification_handler.handle_payment_notification,
        "bot:payment_notification",
    )

    await event_manager.start_listening()
    logger.info("Запущены обработчики уведомлений")


async def on_shutdown(bot: Bot):
    logger.info("Завершение работы бота...")

    # Отключаем обработчик уведомлений
    global ad_handler
    if "ad_handler" in globals():
        await event_manager.stop_listening()
        logger.info("Обработчик уведомлений о рекламе отключен")

    # await bot.session.close()
    # await redis.Redis(db=1).close()
    # logger.info("Redis connection closed")


async def build_bot() -> tuple[Bot, Dispatcher]:
    token = settings.BOT_TOKEN

    timeout = ClientTimeout(
        total=getattr(settings, "BOT_TIMEOUT_TOTAL", 180),  # Общий таймаут
        connect=getattr(
            settings, "BOT_TIMEOUT_CONNECT", 30
        ),  # Таймаут подключения
        sock_read=getattr(
            settings, "BOT_TIMEOUT_SOCK_READ", 150
        ),  # Таймаут чтения
    )

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session_timeout=timeout,
    )

    redis_client = redis.Redis(
        db=settings.BOT_REDIS_DB,
        host=settings.BOT_REDIS_HOST,
        port=settings.BOT_REDIS_PORT,
    )

    storage = RedisStorage(redis_client)

    dp = Dispatcher(storage=storage)

    dp.include_router(command_handlers.router)
    dp.include_router(payment_handlers.router)
    dp.include_router(status_handlers.router)

    # Всегда последний, так как там пустой приемщик
    dp.include_router(other_handlers.router)

    dp.update.middleware(CurrentUserMiddleware())
    dp.callback_query.middleware(CallbackAnswerMiddleware(pre=True))
    dp.callback_query.middleware(IgnoreMessageNotModifiedMiddleware())

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("Сформировали приложение")

    return bot, dp
