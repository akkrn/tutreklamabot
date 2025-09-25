
import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, Message
from aiohttp import ClientOSError

from bot.redis_client import delete_file_id, get_file_id, save_file_id

logger = logging.getLogger(__name__)


def retry_async(retries=3, delay=5):
    """Декоратор для повторной попытки выполнения асинхронной функции"""

    def wrapper(f):
        async def wrapped_f(*args, **kwargs):
            exc = None
            for _ in range(retries):
                try:
                    return await f(*args, **kwargs)
                except TelegramAPIError or ClientOSError as e:
                    logger.error(f"Ошибка, повторная попытка: {e}")
                    exc = e
                    await asyncio.sleep(delay)
            raise exc

        return wrapped_f

    return wrapper

@retry_async(retries=3, delay=2)
async def send_file(
    bot: Bot,
    file_path: str,
    redis_key: str,
    user_tg_id: int,
    caption: str,
    above: bool = False,
) -> Message:
    """Send files to Telegam servers and collect file_id in Redis cache"""
    file_id = await get_file_id(redis_key)
    if file_id:
        try:
            result = await bot.send_photo(
                user_tg_id,
                photo=file_id,
                caption=caption,
                show_caption_above_media=above,
            )
            return result
        except TelegramAPIError:
            await delete_file_id(redis_key)
    try:
        image_from_pc = FSInputFile(file_path)
        result = await bot.send_photo(
            user_tg_id,
            photo=image_from_pc,
            caption=caption,
            show_caption_above_media=above,
        )
        file_id = result.photo[-1].file_id
        await save_file_id(redis_key, file_id)
        return result
    except Exception as e:
        logger.error(f"При отправке карты произошла ошибка: {e}")

