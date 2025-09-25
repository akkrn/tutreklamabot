import redis.asyncio as redis
from django.conf import settings

REDIS_URL = f"redis://{settings.BOT_REDIS_HOST}:{settings.BOT_REDIS_PORT}/{settings.BOT_REDIS_DB}"

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


async def save_file_id(key: str, file_id: str):
    await redis_client.set(key, file_id)


async def get_file_id(key: str) -> str:
    return await redis_client.get(key)


async def delete_file_id(key: str) -> str:
    return await redis_client.delete(key)
