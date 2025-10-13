from typing import Optional

import redis.asyncio as redis
import structlog
from django.conf import settings

logger = structlog.getLogger(__name__)


class SharedRedisManager:
    """Единый менеджер Redis для всего приложения"""

    _instance: Optional["SharedRedisManager"] = None
    _redis_client: Optional[redis.Redis] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def connect(self):
        """Подключается к Redis (только один раз)"""
        if self._redis_client is None:
            try:
                self._redis_client = redis.Redis(
                    host=getattr(settings, "BOT_REDIS_HOST", "localhost"),
                    port=getattr(settings, "BOT_REDIS_PORT", 6379),
                    db=getattr(settings, "BOT_REDIS_DB", 0),
                    decode_responses=True,
                )
                await self._redis_client.ping()
                logger.info("SharedRedisManager подключен к Redis")
            except Exception as e:
                logger.error(
                    f"Ошибка подключения SharedRedisManager к Redis: {e}"
                )
                raise

    async def disconnect(self):
        """Отключается от Redis"""
        if self._redis_client:
            await self._redis_client.close()
            self._redis_client = None
            logger.info("SharedRedisManager отключен от Redis")

    @property
    def client(self) -> redis.Redis:
        """Возвращает Redis клиент"""
        if self._redis_client is None:
            raise RuntimeError(
                "Redis не подключен. Вызовите connect() сначала."
            )
        return self._redis_client

    async def publish(self, channel: str, data: str):
        """Публикует сообщение в канал"""
        await self.client.publish(channel, data)

    async def subscribe_to_channel(self, channel: str, callback):
        """Подписывается на канал с callback"""
        pubsub = self.client.pubsub()
        await pubsub.subscribe(channel)

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await callback(message["data"])
        except Exception as e:
            logger.error(f"Ошибка прослушивания канала {channel}: {e}")
        finally:
            await pubsub.close()


redis_manager = SharedRedisManager()
