import structlog

from django.utils import timezone
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

from bot.keyboards import menu_kb
from bot.translations import get_translation
from bot.middlewares import current_user

logger = structlog.getLogger(__name__)


async def get_menu(
    message: Message,
    state: FSMContext,
    new_msg_text_key: str | None = None,
    is_from_callback: bool = False,
):
    await state.set_state()
    await state.update_data(
        msg_start_upload_btn_id=None,
        msg_upload_btn_id=None,
        spend_bonus=False,
        images=[],
        active_request_id=None,
    )
    data = await state.get_data()
    prev_menu_id: int | None = data.get("menu_msg_id")

    text_key = new_msg_text_key if new_msg_text_key else "menu_prompt"

    text = get_translation(text_key)
    kb = menu_kb()

    if is_from_callback:
        try:
            sent = await message.edit_text(
                text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
            await state.update_data(menu_msg_id=sent.message_id)
        except TelegramBadRequest:
            pass
    else:
        sent = await message.answer(
            text,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        if prev_menu_id and prev_menu_id != sent.message_id:
            await safe_delete_message(message.bot, message.chat.id, prev_menu_id)
        await state.update_data(menu_msg_id=sent.message_id)


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
