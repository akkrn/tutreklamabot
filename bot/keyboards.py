from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.translations import get_translation
from bot.models import Language

MENU_BUTTONS: list[str] = [
]


def menu_kb() -> InlineKeyboardMarkup:
    return create_inline_kb(*MENU_BUTTONS, width=2, separate_first=True)



def create_inline_kb(
    *args: str, width: int = 2, separate_first: bool = False, **kwargs: str
) -> InlineKeyboardMarkup:
    kb_builder = InlineKeyboardBuilder()
    buttons: list[InlineKeyboardButton] = []

    # Обработка args — кнопки по ключу
    for button in args:
        button_text = get_translation(button)
        buttons.append(InlineKeyboardButton(text=button_text, callback_data=button))

    # Обработка kwargs — кнопки с кастомным текстом
    for button, text in kwargs.items():
        buttons.append(InlineKeyboardButton(text=text, callback_data=button))

    # Отдельно первая кнопка, если включено
    if separate_first and buttons:
        kb_builder.row(buttons[0])
        buttons = buttons[1:]

    # Остальные по width
    if buttons:
        kb_builder.row(*buttons, width=width)

    return kb_builder.as_markup()
