import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from aiogram.types import InlineKeyboardMarkup
from aiogram.types import Message
from aiohttp import ClientOSError

from bot.constants import MAX_MESSAGE_LENGTH
from bot.redis_client import delete_file_id
from bot.redis_client import get_file_id
from bot.redis_client import save_file_id
from bot.tg_message_formatter import MARKDOWN_FLAVOR_B
from bot.tg_message_formatter import markdown_to_telegram_markdown
from bot.tg_message_formatter import markdown_to_telegram_markdown_chunked

logger = logging.getLogger(__name__)


def truncate_text(text: str, max_length: int = None) -> str:
    """Обрезает текст до заданной длины и добавляет ..."""
    from bot.constants import MAX_SHORT_TEXT_LENGTH

    if max_length is None:
        max_length = MAX_SHORT_TEXT_LENGTH

    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


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
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """Send files to Telegam servers and collect file_id in Redis cache"""
    formatted_caption = markdown_to_telegram_markdown(caption)

    file_id = await get_file_id(redis_key)
    if file_id:
        try:
            result = await bot.send_photo(
                user_tg_id,
                photo=file_id,
                caption=formatted_caption,
                show_caption_above_media=above,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return result
        except TelegramAPIError:
            await delete_file_id(redis_key)
    try:
        image_from_pc = FSInputFile(file_path)
        result = await bot.send_photo(
            user_tg_id,
            photo=image_from_pc,
            caption=formatted_caption,
            show_caption_above_media=above,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        file_id = result.photo[-1].file_id
        await save_file_id(redis_key, file_id)
        return result
    except Exception as e:
        logger.error(f"При отправке карты произошла ошибка: {e}")


async def send_long(
    bot: Bot,
    chat_id: int,
    txt: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    """Хелпер для отправки длинных сообщений кусками. Заодно преобразовывает Markdown в Telegram Markdown v2"""
    for chunk in markdown_to_telegram_markdown_chunked(
        txt, max_chunk_size=MAX_MESSAGE_LENGTH, patterns=MARKDOWN_FLAVOR_B
    ):
        if chunk.strip():
            await bot.send_message(
                chat_id,
                chunk,
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup,
            )
