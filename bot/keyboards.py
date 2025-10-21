from urllib.parse import urlencode

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from django.conf import settings

from bot.models import Tariff
from bot.translations import get_translation

MENU_BUTTONS: list[str] = [
    "change_tariff_btn",
    "add_channels_btn",
    "my_channels_btn",
    "digest_btn",
    "support_btn",
]


def menu_kb() -> InlineKeyboardMarkup:
    """Главное меню приложения"""
    return create_inline_kb(*MENU_BUTTONS, width=2, separate_first=True)


def add_channels_kb() -> InlineKeyboardMarkup:
    """Клавиатура для экрана добавления каналов"""
    return create_inline_kb("add_channels_btn", width=1)


def support_kb() -> InlineKeyboardMarkup:
    """Клавиатура для экрана поддержки"""
    kb_builder = InlineKeyboardBuilder()

    support_text = get_translation("support_contact_btn")
    support_url = f"https://t.me/{settings.SUPPORT_USERNAME}"
    kb_builder.row(InlineKeyboardButton(text=support_text, url=support_url))

    # Кнопка главного меню
    main_menu_text = get_translation("main_menu_btn")
    kb_builder.row(
        InlineKeyboardButton(text=main_menu_text, callback_data="main_menu_btn")
    )

    return kb_builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """Клавиатура только с кнопкой назад в главное меню"""
    return create_inline_kb("main_menu_btn")


def limit_reached_kb() -> InlineKeyboardMarkup:
    """Клавиатура для сообщения о достижении лимита каналов"""
    return create_inline_kb("change_tariff_btn", "main_menu_btn", width=1)


async def user_channels_kb(user_channels: list) -> InlineKeyboardMarkup:
    """Клавиатура с каналами пользователя и кнопкой отписки"""
    kb_builder = InlineKeyboardBuilder()
    buttons = []
    for channel in user_channels:
        button_text = f"{channel.title} ❌"
        callback_data = f"unsubscribe_{channel.id}"
        buttons.append(
            InlineKeyboardButton(text=button_text, callback_data=callback_data)
        )

    width = 2
    for i in range(0, len(buttons), width):
        row = buttons[i : i + width]
        kb_builder.row(*row)

    main_menu_text = get_translation("main_menu_btn")
    kb_builder.row(
        InlineKeyboardButton(text=main_menu_text, callback_data="main_menu_btn")
    )

    return kb_builder.as_markup()


def create_inline_kb(
    *args: str, width: int = 2, separate_first: bool = False, **kwargs: str
) -> InlineKeyboardMarkup:
    kb_builder = InlineKeyboardBuilder()
    buttons: list[InlineKeyboardButton] = []

    # Обработка args — кнопки по ключу
    for button in args:
        button_text = get_translation(button)
        buttons.append(
            InlineKeyboardButton(text=button_text, callback_data=button)
        )

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


async def tariff_kb() -> InlineKeyboardMarkup:
    """Клавиатура с тарифами, каждая кнопка открывает WebApp (Robokassa)."""

    kb_builder = InlineKeyboardBuilder()

    def get_tariff():
        return list(Tariff.objects.filter(is_active=True).order_by("price"))

    active_tariffs = await sync_to_async(get_tariff)()

    for tariff in active_tariffs:
        # Текст кнопки: Название тарифа — Цена
        button_text = f"{tariff.get_price_display()} — {tariff.channels_limit} Каналов ({tariff.duration_days} дней)"

        # Формируем ссылку провайдера (Robokassa) для открытия внутри Telegram
        # Минимально необходимые параметры; при необходимости замените на свои
        params = {
            "MerchantLogin": getattr(
                settings, "ROBOKASSA_MERCHANT_LOGIN", "demo"
            ),
            "OutSum": getattr(tariff, "price_rubles", None)
            or (tariff.price / 100),
            "InvoiceID": f"tg_tariff_{tariff.id}",
            "Description": f"Оплата тарифа {tariff.name}",
            "Culture": "ru",
        }
        provider_url = (
            "https://auth.robokassa.ru/Merchant/Index.aspx?" + urlencode(params)
        )

        kb_builder.row(
            InlineKeyboardButton(
                text=button_text,
                web_app=WebAppInfo(url=provider_url),
            )
        )

    main_menu_text = get_translation("main_menu_btn")
    kb_builder.row(
        InlineKeyboardButton(text=main_menu_text, callback_data="main_menu_btn")
    )

    return kb_builder.as_markup()
