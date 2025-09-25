from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.models import Channel
from bot.translations import get_translation

MENU_BUTTONS: list[str] = [
    "add_channels_btn",
    "my_channels_btn",
    "digest_btn",
    "support_btn"
]

def menu_kb() -> InlineKeyboardMarkup:
    """Главное меню приложения"""
    return create_inline_kb(*MENU_BUTTONS, width=2)


def add_channels_kb() -> InlineKeyboardMarkup:
    """Клавиатура для экрана добавления каналов"""
    return create_inline_kb("add_channels_btn", width=1)


def search_channels_kb() -> InlineKeyboardMarkup:
    """Клавиатура для поиска каналов + главное меню"""
    return create_inline_kb("search_channels_btn", "main_menu_btn", separate_first=True)


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """Клавиатура только с кнопкой назад в главное меню"""
    return create_inline_kb("main_menu_btn")


async def user_channels_kb(user_channels: list) -> InlineKeyboardMarkup:
    """Клавиатура с каналами пользователя и кнопкой отписки"""
    kb_builder = InlineKeyboardBuilder()
    buttons = []
    async for channel in user_channels:
        button_text = f"{channel.title} ❌"
        callback_data = f"unsubscribe_{channel.id}"
        buttons.append(InlineKeyboardButton(text=button_text, callback_data=callback_data))

    width = 2
    for i in range(0, len(buttons), width):
        row = buttons[i:i+width]
        kb_builder.row(*row)

    main_menu_text = get_translation("main_menu_btn")
    kb_builder.row(InlineKeyboardButton(text=main_menu_text, callback_data="main_menu_btn"))

    return kb_builder.as_markup()



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
