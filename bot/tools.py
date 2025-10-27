import asyncio
import logging
import mimetypes
import re

import emoji
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message
from aiohttp import ClientOSError

from bot.redis_client import delete_file_id, get_file_id, save_file_id

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


def get_media_type(file_path: str) -> str:
    """Определяет тип медиафайла по MIME или расширению."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type:
        main_type = mime_type.split("/")[0]
        if main_type in ("image", "video"):
            return "photo" if main_type == "image" else "video"
    return "document"


@retry_async(retries=3, delay=2)
async def send_file(
    bot: Bot,
    file_path: str,
    redis_key: str,
    user_tg_id: int,
    caption: str,
    above: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: ParseMode = ParseMode.HTML,
) -> Message:
    """Send files to Telegam servers and collect file_id in Redis cache"""

    media_type = get_media_type(file_path)
    if media_type == "document":
        logger.error("Попытка отправить файл", file_path=file_path)

    file_id = await get_file_id(redis_key)
    if file_id:
        try:
            if media_type == "video":
                result = await bot.send_video(
                    user_tg_id,
                    video=file_id,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
            else:
                result = await bot.send_photo(
                    user_tg_id,
                    photo=file_id,
                    caption=caption,
                    show_caption_above_media=above,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
            return result
        except TelegramAPIError:
            await delete_file_id(redis_key)
    try:
        file_from_pc = FSInputFile(file_path)
        if media_type == "video":
            result = await bot.send_video(
                user_tg_id,
                video=file_from_pc,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            file_id = result.video.file_id
        else:
            result = await bot.send_photo(
                user_tg_id,
                photo=file_from_pc,
                caption=caption,
                show_caption_above_media=above,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            file_id = result.photo[-1].file_id
        await save_file_id(redis_key, file_id)
        return result
    except Exception as e:
        logger.error(f"При отправке файла произошла ошибка: {e}")


async def send_long(
    bot: Bot,
    chat_id: int,
    txt: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    """Хелпер для отправки длинных сообщений кусками. Заодно преобразовывает Markdown в Telegram Markdown v2"""
    # for chunk in markdown_to_telegram_markdown_chunked(
    #     txt, max_chunk_size=MAX_MESSAGE_LENGTH, patterns=MARKDOWN_FLAVOR_B
    # ):
    await bot.send_message(
        chat_id,
        txt,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )
    # for chunk in txt.split("\n"):
    #     if chunk.strip():
    #         max_retries = 3
    #         retry_delay = 2

    #         for attempt in range(max_retries):
    #             try:

    #                 break
    #             except (TelegramAPIError, ClientOSError) as e:
    #                 logger.warning(
    #                     f"Ошибка отправки сообщения (попытка {attempt + 1}/{max_retries}): {e}"
    #                 )
    #                 if attempt < max_retries - 1:
    #                     await asyncio.sleep(retry_delay * (attempt + 1))
    #                 else:
    #                     logger.error(
    #                         f"Не удалось отправить сообщение после {max_retries} попыток: {e}"
    #                     )
    #                     raise


_MD_CLEAN_RE = re.compile(
    r"""
    (?:__|[*_~`]|```|``)      # выделения, курсив, код, зачёркивания
    |                         # или
    \[(.*?)\]\(.*?\)          # markdown-ссылки [текст](url)
    |                         # или
    [\[\]\(\)\>#\+=|{}\\\*] # лишние спецсимволы MarkdownV2
    """,
    re.VERBOSE,
)


def clean_markdown(text: str) -> str:
    """Удаляет все элементы Markdown/MarkdownV2-разметки."""
    text = re.sub(_MD_CLEAN_RE, r"\1", text)
    text = text.replace("*", "")
    text = emoji.replace_emoji(text, replace="")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()
