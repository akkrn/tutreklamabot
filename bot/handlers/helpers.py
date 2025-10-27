import base64
from datetime import timedelta
from html import escape
from pathlib import Path

import structlog
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from bot.constants import MEDIA_FILES_PATH
from bot.keyboards import menu_kb
from bot.middlewares import current_user
from bot.models import ChannelNews, UserSubscription
from bot.redis_client import get_file_id
from bot.tools import clean_markdown, send_file, truncate_text
from bot.translations import get_translation

logger = structlog.getLogger(__name__)


async def get_menu(
    message: Message,
    state: FSMContext,
    new_msg_text_key: str | None = None,
    is_from_callback: bool = False,
):
    """Показывает единое главное меню"""
    await state.clear()
    await state.update_data(
        msg_start_upload_btn_id=None,
        msg_upload_btn_id=None,
    )
    # data = await state.get_data()
    # prev_menu_id: int | None = data.get("menu_msg_id")

    user = current_user.get()
    username = user.username or user.tg_user_id
    user_link = f"tg://resolve?domain={username}"

    # Получаем информацию о тарифе и каналах

    def get_user_info():
        current_subscription = user.get_subscription_info()
        is_active = current_subscription.get("is_active")
        channels_limit = current_subscription.get("channels_limit")

        channels_count = user.subscribed_channels_count

        # Получаем активную подписку для детальной информации
        active_sub = None
        if is_active:
            active_sub = UserSubscription.objects.filter(
                user=user, status=UserSubscription.STATUS_ACTIVE
            ).first()

        next_charge_date = None
        next_charge_amount = None
        if active_sub and active_sub.is_recurring_enabled:
            next_charge_date = active_sub.expires_at.strftime("%d.%m.%y")
            next_charge_amount = active_sub.tariff.price

        tariff_name = current_subscription.get("tariff_name", "Бесплатный")
        if is_active and active_sub:
            tariff_name = f"{channels_limit} Каналов на {active_sub.tariff.duration_days} дней"

        return (
            tariff_name,
            channels_limit,
            channels_count,
            is_active,
            next_charge_date,
            next_charge_amount,
        )

    (
        tariff_name,
        channels_limit,
        channels_count,
        is_active,
        next_charge_date,
        next_charge_amount,
    ) = await sync_to_async(get_user_info)()

    encoded_id = (
        base64.urlsafe_b64encode(str(message.from_user.id).encode())
        .decode()
        .rstrip("=")
    )
    bot_user = await message.bot.get_me()
    user_link_formatted = (
        f'<a href="{escape(user_link)}">{escape(username)}</a>'
    )

    ref_link = f"t.me/{bot_user.username}?start=ref_{encoded_id}"
    ref_text = f"Пригласить друга: <code>{escape(ref_link)}</code>"
    if new_msg_text_key:
        caption = get_translation(new_msg_text_key)
    else:
        caption_lines = [
            f"<b>Пользователь:</b> {user_link_formatted}\n",
            f"Каналов добавлено: {channels_count}/{channels_limit}\n\n",
            f"Тариф: {tariff_name}\n",
        ]

        # Если есть активная подписка с автоплатежом, добавляем информацию о следующем списании
        if is_active and next_charge_date and next_charge_amount:
            caption_lines.append(f"Следующее списание: {next_charge_date}\n")
            caption_lines.append(
                f"Сумма следующего списания: {next_charge_amount} ₽\n"
            )

        caption_lines.append(f"\n{ref_text}")

        caption = "".join(caption_lines)

    if is_from_callback:
        await send_image_message(
            message=message,
            image_name="main_menu",
            caption=caption,
            keyboard=menu_kb(),
            edit_message=True,
        )
        # if result:
        #     if prev_menu_id and prev_menu_id != result.message_id:
        #         await safe_delete_message(
        #             message.bot, message.chat.id, prev_menu_id
        #         )  # TODO проверить, что стоит так делать
        #     await state.update_data(menu_msg_id=result.message_id)
    else:
        await send_image_message(
            message=message,
            image_name="main_menu",
            caption=caption,
            keyboard=menu_kb(),
            edit_message=False,
        )
        # if result:
        #     if prev_menu_id and prev_menu_id != result.message_id:
        #         await safe_delete_message(
        #             message.bot, message.chat.id, prev_menu_id
        #         )
        #     await state.update_data(menu_msg_id=result.message_id)


async def safe_delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        pass


async def safe_remove_keyboard(
    callback: CallbackQuery, state: FSMContext
) -> None:
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
    edit_message: bool = False,
    parse_mode: ParseMode = ParseMode.HTML,
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
                media = InputMediaPhoto(
                    media=file_id, caption=caption, parse_mode=parse_mode
                )
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
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
                try:
                    await bot.delete_message(
                        message.chat.id, message.message_id
                    )
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
                reply_markup=keyboard,
                parse_mode=parse_mode,
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
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
            return result

        except Exception as e:
            logger.error(f"Ошибка при отправке изображения {image_name}: {e}")
            return await message.answer(caption, reply_markup=keyboard)


async def generate_digest_text_paginated(
    page: int = 0, max_length: int = 1000
) -> tuple[str, int]:
    """Генерирует текст дайджеста рекламных постов за 24 часа с пагинацией"""
    user = current_user.get()
    yesterday = timezone.now() - timedelta(hours=24)

    # Получаем все новости за последние 24 часа
    user_news = (
        ChannelNews.objects.filter(
            channel__users=user, created_at__gte=yesterday
        )
        .select_related("channel")
        .order_by("-created_at")
    )

    all_news_blocks = []
    async for news in user_news:
        channel = news.channel
        channel_link = (
            f"https://t.me/{channel.main_username}"
            if channel.main_username
            else "https://t.me/c/{channel.telegram_id}"
        )

        # Формируем HTML разметку для каждой новости
        channel_header = (
            f'<a href="{channel_link}"><b>{channel.title}</b></a>:\n'
        )

        truncated = truncate_text(clean_markdown(news.message) or "")
        news_content = f" · {truncated}\n"

        post_link = (
            f"{channel_link}/{news.message_id}"
            if channel.main_username
            else f"{channel_link}/{news.message_id}"
        )
        news_link = f'<a href="{post_link}">Перейти к посту →</a>'

        news_block = channel_header + news_content + news_link + "\n"
        all_news_blocks.append(news_block)

    # Разделяем на страницы по max_length
    pages = []
    current_page = ""
    current_length = 0

    for news_block in all_news_blocks:
        news_block_with_separator = news_block + "\n"

        # Если блок помещается на текущую страницу
        if current_length + len(news_block_with_separator) <= max_length:
            current_page += news_block_with_separator
            current_length += len(news_block_with_separator)
        else:
            # Если блок не помещается, сохраняем текущую страницу и начинаем новую
            if current_page:
                pages.append((current_page).rstrip())

            # Проверяем, помещается ли блок на новую страницу
            if len(news_block_with_separator) <= max_length:
                current_page = news_block_with_separator
                current_length = len(current_page)
            else:
                # Блок слишком большой, обрезаем его
                available_space = max_length
                truncated_block = news_block_with_separator[:available_space]
                current_page = truncated_block
                current_length = len(current_page)

    # Добавляем последнюю страницу
    if current_page:
        pages.append((current_page).rstrip())

    if not pages:
        return "❤️ <b>Новых постов ещё не было</b>. Возвращайтесь позже.", 0

    total_pages = len(pages)

    # Циклическая пагинация
    actual_page = page % total_pages if total_pages > 0 else 0

    return pages[actual_page], total_pages
