from contextvars import ContextVar
from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject, Update

from bot.models import User

logger = structlog.get_logger(__name__)

current_user: ContextVar[User] = ContextVar("current_user")


class CurrentUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
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
            user = await User.objects.aget(tg_user_id=tg_user.id)
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


class IgnoreMessageNotModifiedMiddleware:  # При нажатии на кнопку, которая не меняет текст сообщения, игнорируем ошибку, чтобы не засорять логи
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Any],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            raise
