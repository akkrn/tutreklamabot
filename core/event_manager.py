import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

import structlog

from core.redis_manager import redis_manager
from userbot.redis_messages import (
    NewAdMessage,
    SubscribeChannelsMessage,
    SubscribeResponseMessage,
    deserialize_message,
    serialize_message,
)

logger = structlog.getLogger(__name__)


class EventType(Enum):
    SUBSCRIBE_CHANNELS = "subscribe_channels"
    SUBSCRIBE_RESPONSE = "subscribe_response"
    NEW_AD_MESSAGE = "new_ad_message"


@dataclass
class EventHandler:
    """Обработчик события"""

    event_type: EventType
    callback: Callable
    channel: str


class EventManager:
    """Единый менеджер событий для всего приложения"""

    _instance: Optional["EventManager"] = None
    _handlers: dict[str, list[EventHandler]] = {}
    _running_tasks: list[asyncio.Task] = []

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register_handler(
        self, event_type: EventType, callback: Callable, channel: str
    ):
        """Регистрирует обработчик события"""
        handler = EventHandler(event_type, callback, channel)

        if channel not in self._handlers:
            self._handlers[channel] = []

        self._handlers[channel].append(handler)
        logger.info(
            f"Зарегистрирован обработчик для {event_type.value} на канале {channel}"
        )

    async def start_listening(self):
        """Запускает прослушивание всех зарегистрированных каналов"""
        await redis_manager.connect()

        for channel, handlers in self._handlers.items():
            task = asyncio.create_task(self._listen_channel(channel, handlers))
            self._running_tasks.append(task)

        logger.info(f"Запущено прослушивание {len(self._handlers)} каналов")

    async def stop_listening(self):
        """Останавливает прослушивание"""
        for task in self._running_tasks:
            task.cancel()

        await asyncio.gather(*self._running_tasks, return_exceptions=True)
        self._running_tasks.clear()

        await redis_manager.disconnect()
        logger.info("Остановлено прослушивание всех каналов")

    async def _listen_channel(self, channel: str, handlers: list[EventHandler]):
        """Прослушивает конкретный канал"""
        try:
            await redis_manager.subscribe_to_channel(
                channel, self._handle_message
            )
        except Exception as e:
            logger.error(f"Ошибка прослушивания канала {channel}: {e}")

    async def _handle_message(self, data: str):
        """Обрабатывает входящее сообщение"""
        try:
            # Определяем тип сообщения по содержимому
            if '"subscribe_channels"' in data:
                message = deserialize_message(data, SubscribeChannelsMessage)
                if message:
                    await self._call_handlers(
                        EventType.SUBSCRIBE_CHANNELS, message
                    )
            elif '"subscribe_response"' in data:
                message = deserialize_message(data, SubscribeResponseMessage)
                if message:
                    await self._call_handlers(
                        EventType.SUBSCRIBE_RESPONSE, message
                    )
            elif '"new_ad_message"' in data:
                message = deserialize_message(data, NewAdMessage)
                if message:
                    await self._call_handlers(EventType.NEW_AD_MESSAGE, message)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def _call_handlers(self, event_type: EventType, message: Any):
        """Вызывает все обработчики для типа события"""
        for channel, handlers in self._handlers.items():
            for handler in handlers:
                if handler.event_type == event_type:
                    try:
                        await handler.callback(message)
                    except Exception as e:
                        logger.error(
                            f"Ошибка в обработчике {event_type.value}: {e}"
                        )

    async def publish_event(
        self, event_type: EventType, message: Any, channel: str
    ):
        """Публикует событие в Redis"""
        data = serialize_message(message)
        await redis_manager.publish(channel, data)
        logger.info(
            f"Опубликовано событие {event_type.value} в канал {channel}"
        )

    async def wait_for_response(
        self, request_id: str, timeout: int = 30
    ) -> Optional[Any]:
        """Ждет ответ на запрос подписки"""
        response_channel = f"bot:response:{request_id}"

        try:
            # Создаем временный pubsub для ожидания ответа
            pubsub = redis_manager.client.pubsub()
            await pubsub.subscribe(response_channel)

            async with asyncio.timeout(timeout):
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        response = deserialize_message(
                            message["data"], SubscribeResponseMessage
                        )
                        if response and response.request_id == request_id:
                            return response

        except asyncio.TimeoutError:
            logger.warning(f"Таймаут ожидания ответа для запроса {request_id}")
        except Exception as e:
            logger.error(f"Ошибка ожидания ответа: {e}")
        finally:
            await pubsub.unsubscribe(response_channel)
            await pubsub.close()

        return None


# Глобальный экземпляр
event_manager = EventManager()
