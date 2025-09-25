from contextvars import ContextVar
from aiogram import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery, TelegramObject
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest

from typing import Callable, Awaitable, Dict, Any
import structlog

from bot.models import User, Bot, Language

logger = structlog.get_logger(__name__)

current_user: ContextVar[User] = ContextVar("current_user")


class CurrentUserMiddleware(BaseMiddleware):
    def __init__(self, bot_instance: Bot):
        self.bot_instance = bot_instance

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        if event.message:
            tg_user = event.message.from_user
            chat = event.message.chat
        elif event.callback_query:
            tg_user = event.callback_query.from_user
            chat = event.callback_query.message.chat
        elif getattr(event, "my_chat_member", None):
            tg_user = event.my_chat_member.from_user
            chat = event.my_chat_member.chat
        else:
            # Для апдейтов без пользователя — не устанавливаем current_user
            return await handler(event, data)

        # Получение или создание пользователя
        try:
            user = await User.objects.select_related("language").aget(
                tg_user_id=tg_user.id
            )
        except User.DoesNotExist:
            user = await User.objects.acreate(
                tg_user_id=tg_user.id,
                tg_chat_id=chat.id,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                username=tg_user.username,
                is_tg_premium=tg_user.is_premium or False,
                language=tg_user.language_code,
            )
            logger.info(
                "Регистрируем нового пользователя: %s %s",
                tg_user,
                user,
                tg_user=tg_user.id,
            )

        token = current_user.set(user)
        try:
            return await handler(event, data)
        finally:
            current_user.reset(token)