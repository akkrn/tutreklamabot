import structlog
from pathlib import Path
import base64
from datetime import timedelta
from collections import defaultdict

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from django.conf import settings
from django.utils import timezone

from bot.constants import MEDIA_FILES_PATH
from bot.keyboards import menu_kb
from bot.middlewares import current_user
from bot.models import ChannelNews
from bot.redis_client import get_file_id
from bot.translations import get_translation
from bot.utils import send_file


logger = structlog.getLogger(__name__)


async def get_menu(
    message: Message,
    state: FSMContext,
    new_msg_text_key: str | None = None,
    is_from_callback: bool = False,
):
    """Показывает единое главное меню"""
    await state.set_state()
    await state.update_data(
        msg_start_upload_btn_id=None,
        msg_upload_btn_id=None,
    )
    data = await state.get_data()
    prev_menu_id: int | None = data.get("menu_msg_id")

    user = current_user.get()
    username = user.username or user.tg_user_id
    user_link = f"tg://resolve?domain={username}" 

    encoded_id = (
        base64.urlsafe_b64encode(str(message.from_user.id).encode())
        .decode()
        .rstrip("=")
    )
    bot_user = await message.bot.get_me()
    ref_link = f"t.me/{bot_user.username}?start=ref_{encoded_id}"

    if new_msg_text_key:
        caption = get_translation(new_msg_text_key)
    else:
        caption = f"Пользователь: [{username}]({user_link})\n\nПригласить друга: {ref_link}"
        
    if is_from_callback:
        result = await send_image_message(
            message=message,
            image_name="main_menu",
            caption=caption,
            keyboard=menu_kb(),
            edit_message=True
        )
        if result:
            if prev_menu_id and prev_menu_id != result.message_id:
                await safe_delete_message(message.bot, message.chat.id, prev_menu_id) # TODO проверить, что стоит так делать
            await state.update_data(menu_msg_id=result.message_id)
    else:
        result = await send_image_message(
            message=message,
            image_name="main_menu",
            caption=caption,
            keyboard=menu_kb(),
            edit_message=False
        )
        if result:
            if prev_menu_id and prev_menu_id != result.message_id:
                await safe_delete_message(message.bot, message.chat.id, prev_menu_id)
            await state.update_data(menu_msg_id=result.message_id)


async def safe_delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        pass


async def safe_remove_keyboard(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


def strip_btns_from_kb(
    markup: InlineKeyboardMarkup | None,
) -> InlineKeyboardMarkup | None:
    """
    Убирает кнопки из клавиатуры с callback_data.
    Возвращает новую клавиатуру без этих кнопок или None.
    Оставляет кнопку со ссылкой в массовой рассылке
    """
    if not markup or not markup.inline_keyboard:
        return None
    new_rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for btn in row:
            if getattr(btn, "callback_data", None):
                continue
            new_row.append(btn)
        if new_row:
            new_rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=new_rows) if new_rows else None


async def send_image_message(
    message: Message,
    image_name: str,
    caption: str = "",
    keyboard: InlineKeyboardMarkup | None = None,
    above: bool = False,
    bot: Bot | None = None,
    edit_message: bool = False
) -> Message | None:
    """Отправляет изображение с кешированием через Redis"""
    if not bot:
        bot = message.bot

    mediafiles_dir: Path = (settings.BASE_DIR / MEDIA_FILES_PATH).resolve()
    file_path: Path = (mediafiles_dir / f"{image_name}.jpg").resolve()

    if not file_path.exists():
        logger.error(f"Файл изображения не найден: {file_path}")
        return await message.answer(caption, reply_markup=keyboard)

    redis_key = f"image:{image_name}"
    if edit_message:
        try:
            file_id = await get_file_id(redis_key)

            if file_id:
                media = InputMediaPhoto(media=file_id, caption=caption)
                result = await bot.edit_message_media(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    media=media,
                    reply_markup=keyboard,
                )
                return result
            else:
                result = await send_file(
                    bot=bot,
                    file_path=str(file_path),
                    redis_key=redis_key,
                    user_tg_id=message.chat.id,
                    caption=caption,
                    above=above,
                    reply_markup=keyboard
                )
                try:
                    await bot.delete_message(message.chat.id, message.message_id)
                except TelegramBadRequest:
                    pass
                return result

        except TelegramBadRequest:
            result = await send_file(
                bot=bot,
                file_path=str(file_path),
                redis_key=redis_key,
                user_tg_id=message.chat.id,
                caption=caption,
                above=above,
                reply_markup=keyboard
            )

            try:
                await bot.delete_message(message.chat.id, message.message_id)
            except TelegramBadRequest:
                pass
            return result
    else:
        try:
            result = await send_file(
                bot=bot,
                file_path=str(file_path),
                redis_key=redis_key,
                user_tg_id=message.chat.id,
                caption=caption,
                above=above,
                reply_markup=keyboard
            )
            return result

        except Exception as e:
            logger.error(f"Ошибка при отправке изображения {image_name}: {e}")
            return await message.answer(caption, reply_markup=keyboard)


# TODO Можно и нужно кешировать результат
async def generate_digest_text() -> str:
    """Генерирует текст дайджеста рекламных постов за 24 часа"""
    user = current_user.get()

    yesterday = timezone.now() - timedelta(hours=24)

    digest_text = "*Рекламные посты за прошедшие 24 часа:*\n\n"
    max_length = 1000
    current_length = len(digest_text)
    has_news = False

    channels_news = defaultdict(list)
    user_news = ChannelNews.objects.filter(
        channel__users=user,
        created_at__gte=yesterday
    ).select_related('channel').order_by('-created_at')

    async for news in user_news:
        channels_news[news.channel].append(news)

    for channel, news_list in channels_news.items():
        has_news = True
        channel_link = f"https://t.me/{channel.main_username}" if channel.main_username else channel.link_subscription or None
        channel_block = f"[{channel.title}]({channel_link})\n" if channel_link else f"{channel.title}\n"

        for news in news_list:
            news_line = f"· {news.short_message}\n" # TODO переделать через обрезку полного сообщения
            post_link = f"{channel_link}/{news.message_id}" if channel_link else None
            news_link = f"[Перейти к посту →]({post_link})" if post_link else None
            news_block =  news_line + news_link
            
            if current_length + len(channel_block) + len(news_block) + 1 > max_length:
                break

            channel_block += news_block

        if current_length + len(channel_block) + 1 > max_length:
            break

        digest_text += channel_block + "\n"
        current_length += len(channel_block) + 1

    if not has_news:
        digest_text += "За последние 24 часа новых рекламных постов не было."

    return digest_text
