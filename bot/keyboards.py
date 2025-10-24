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


def cancel_reccurent_kb() -> InlineKeyboardMarkup:
    """Клавиатура для экрана отмены подписки"""
    return create_inline_kb(
        "cancel_reccurent_done_btn", "main_menu_btn", width=1
    )


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """Клавиатура только с кнопкой назад в главное меню"""
    return create_inline_kb("main_menu_btn")


def add_more_channels_kb() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой назад и добавить еще"""
    return create_inline_kb(
        "main_menu_btn", width=1, add_channels_btn="Добавить еще"
    )


def add_channels_with_menu_kb() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой назад и добавить еще"""
    return create_inline_kb("main_menu_btn", "add_channels_btn", width=1)


def new_menu_kb() -> InlineKeyboardMarkup:
    """Клавиатура только с кнопкой назад в главное меню"""
    return create_inline_kb("new_main_menu_btn")


def limit_reached_kb() -> InlineKeyboardMarkup:
    """Клавиатура для сообщения о достижении лимита каналов"""
    return create_inline_kb("change_tariff_btn", "main_menu_btn", width=1)


async def user_channels_kb(
    user_channels: list, page: int = 0, channels_per_page: int = 10
) -> InlineKeyboardMarkup:
    """Клавиатура с каналами пользователя и кнопкой отписки с циклической пагинацией"""
    kb_builder = InlineKeyboardBuilder()

    # Циклическая пагинация
    total_pages = (
        len(user_channels) + channels_per_page - 1
    ) // channels_per_page
    if total_pages > 0:
        actual_page = page % total_pages
        start_idx = actual_page * channels_per_page
        end_idx = start_idx + channels_per_page
        page_channels = user_channels[start_idx:end_idx]
    else:
        page_channels = []

    buttons = []
    for channel in page_channels:
        button_text = f"❌ {channel.title}"
        callback_data = f"unsubscribe_{channel.id}"
        buttons.append(
            InlineKeyboardButton(text=button_text, callback_data=callback_data)
        )

    width = 2
    for i in range(0, len(buttons), width):
        row = buttons[i : i + width]
        kb_builder.row(*row)

    # Добавляем кнопки пагинации если нужно
    if total_pages > 1:
        pagination_buttons = []

        # Кнопка "Назад" - всегда видна (циклическая)
        prev_page = (page - 1) % total_pages
        pagination_buttons.append(
            InlineKeyboardButton(
                text="◀️", callback_data=f"channels_page_{prev_page}"
            )
        )

        # Информация о странице
        pagination_buttons.append(
            InlineKeyboardButton(
                text=f"{page+1}/{total_pages}", callback_data="noop"
            )
        )

        # Кнопка "Вперед" - всегда видна (циклическая)
        next_page = (page + 1) % total_pages
        pagination_buttons.append(
            InlineKeyboardButton(
                text="▶️", callback_data=f"channels_page_{next_page}"
            )
        )

        kb_builder.row(*pagination_buttons)

    main_menu_text = get_translation("main_menu_btn")
    kb_builder.row(
        InlineKeyboardButton(text=main_menu_text, callback_data="main_menu_btn")
    )

    return kb_builder.as_markup()


def digest_kb(page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Клавиатура для дайджеста с циклической пагинацией"""
    kb_builder = InlineKeyboardBuilder()

    if total_pages > 1:
        pagination_buttons = []

        # Кнопка "Назад" - всегда видна (циклическая)
        prev_page = (page - 1) % total_pages
        pagination_buttons.append(
            InlineKeyboardButton(
                text="◀️", callback_data=f"digest_page_{prev_page}"
            )
        )

        # Информация о странице
        pagination_buttons.append(
            InlineKeyboardButton(
                text=f"{page+1}/{total_pages}", callback_data="noop"
            )
        )

        # Кнопка "Вперед" - всегда видна (циклическая)
        next_page = (page + 1) % total_pages
        pagination_buttons.append(
            InlineKeyboardButton(
                text="▶️", callback_data=f"digest_page_{next_page}"
            )
        )

        kb_builder.row(*pagination_buttons)

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
    cancel_reccurent_text = get_translation("cancel_reccurent_btn")
    kb_builder.row(
        InlineKeyboardButton(
            text=cancel_reccurent_text, callback_data="cancel_reccurent_btn"
        )
    )

    main_menu_text = get_translation("main_menu_btn")
    kb_builder.row(
        InlineKeyboardButton(text=main_menu_text, callback_data="main_menu_btn")
    )

    return kb_builder.as_markup()
