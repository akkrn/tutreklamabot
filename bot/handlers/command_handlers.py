import base64
import uuid
from datetime import timedelta

import structlog
from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.handlers.helpers import (
    generate_digest_text,
    get_menu,
    send_image_message,
)
from bot.keyboards import (
    add_channels_kb,
    back_to_menu_kb,
    limit_reached_kb,
    support_kb,
    tariff_kb,
    user_channels_kb,
)
from bot.middlewares import current_user
from bot.models import Channel, ChannelSubscription, User
from bot.utils.link_parser import handle_forwarded_message, parse_channel_links
from core.event_manager import EventType, event_manager
from userbot.redis_messages import ChannelResult, SubscribeChannelsMessage

router = Router()
logger = structlog.getLogger(__name__)

MAX_CHANNELS_PER_USER = 7


async def check_channel_limit(
    user: User, new_channels_count: int
) -> tuple[bool, str, bool]:
    """Проверяет, не превышает ли пользователь лимит каналов"""
    current_channels_count = await sync_to_async(
        lambda: Channel.objects.filter(users=user).count()
    )()

    total_channels = current_channels_count + new_channels_count

    if total_channels > MAX_CHANNELS_PER_USER:
        remaining_slots = MAX_CHANNELS_PER_USER - current_channels_count
        if remaining_slots <= 0:
            return (
                False,
                f"Достигнут лимит запросов в вашем тарифе. Пожалуйста, смените тариф.\n\nКаналов добавлено: {current_channels_count}/{MAX_CHANNELS_PER_USER}",
            )
        else:
            return (
                False,
                f"Вы можете добавить только {remaining_slots} каналов. У вас уже {current_channels_count} из {MAX_CHANNELS_PER_USER}.",
            )

    return True, ""


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, command: CommandObject):
    user = current_user.get()

    if command.args:
        await handle_start_referrals(message, user, command.args)

    user_channels_count = await user.channels.acount()

    if user_channels_count == 0:
        await send_image_message(
            message=message,
            image_name="add_channels",
            caption="",
            keyboard=add_channels_kb(),
        )
    else:
        await get_menu(message, state)


@router.callback_query(F.data == "add_channels_btn")
async def handle_add_channels(callback: CallbackQuery, state: FSMContext):
    await send_image_message(
        message=callback.message,
        image_name="search",
        caption="Отправьте ссылку на канал или несколько ссылок (каждую с новой строки)",
        keyboard=back_to_menu_kb(),
        edit_message=True,
    )


@router.callback_query(F.data == "main_menu_btn")
async def handle_main_menu(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Главное меню'"""
    # Убираем кнопку из рекламного сообщения
    await callback.message.edit_reply_markup(reply_markup=None)

    # Отправляем новое сообщение с меню
    await get_menu(callback.message, state, is_from_callback=False)


@router.callback_query(F.data == "my_channels_btn")
async def handle_my_channels(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Мои каналы'"""
    user = current_user.get()
    user_channels_count = await user.channels.acount()

    if user_channels_count == 0:
        caption = "У вас пока нет добавленных каналов."
        keyboard = add_channels_kb()
    else:
        caption = "Для удаления канала — нажмите на него."
        channels = await sync_to_async(list)(user.channels.all())
        keyboard = await user_channels_kb(channels)

    await send_image_message(
        message=callback.message,
        image_name="channels",
        caption=caption,
        keyboard=keyboard,
        edit_message=True,
    )


@router.callback_query(F.data == "digest_btn")
async def handle_digest(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Дайджест'"""
    digest_caption = await generate_digest_text()
    # TODO предусмотреть логику, если новостей очень много и не помещаются в одно сообщение
    await send_image_message(
        message=callback.message,
        image_name="digest",
        caption=digest_caption,
        keyboard=back_to_menu_kb(),
        edit_message=True,
    )


@router.callback_query(F.data == "support_btn")
async def handle_support(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Помощь'"""
    support_text = (
        "🌀 **Как это работает?**\n"
        "Я отслеживаю телеграм-каналы и присылаю рекламные посты. "
        "Вы видите, кто размещается у конкурентов, и можете предложить рекламу у себя.\n\n"
        "💬 **Как связаться с рекламодателем?**\n"
        "· Если рекламируют канал → контакты в описании.\n"
        "· Сайт → ищите почту или соцсети.\n"
        "· Нет контактов → спросите у админа канала\n\n"
        "🖖 **Что писать рекламодателю?**\n"
        "· Опишите свою аудиторию.\n"
        "· Дайте цифры и статистику.\n"
        "· Покажите, чем вы лучше конкурентов."
    )

    await send_image_message(
        message=callback.message,
        image_name="support",
        caption=support_text,
        keyboard=support_kb(),
        edit_message=True,
    )


@router.callback_query(F.data == "change_tariff_btn")
async def handle_change_tariff(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Сменить тариф'"""
    tariff_text = (
        "Оплачивая, вы принимаете [публичную оферту](https://telegra.ph/Publichnaya-oferta-o-zaklyuchenii-dogovora-informacionno-konsultacionnyh-uslug-08-03) "
        "и соглашение о присоединении к [рекуррентной системе](https://telegra.ph/Soglashenie-o-prisoedinenii-k-rekurrentnoj-sisteme-platezhej-07-24) платежей.\n\n"
        "Перед оплатой рекомендуем отключить VPN."
    )

    await send_image_message(
        message=callback.message,
        image_name="payment",
        caption=tariff_text,
        keyboard=tariff_kb(),
        edit_message=True,
    )


@router.callback_query(F.data == "tariff_month_30")
async def handle_tariff_month_30(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки '749 ₽ - Месяц / 30 Каналов'"""
    await callback.answer(
        "Функция оплаты будет добавлена в ближайшее время", show_alert=True
    )


@router.callback_query(F.data == "tariff_3month_50")
async def handle_tariff_3month_50(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки '2290 ₽ - 3 Месяца / 50 Каналов'"""
    await callback.answer(
        "Функция оплаты будет добавлена в ближайшее время", show_alert=True
    )


@router.callback_query(F.data == "tariff_6month_70")
async def handle_tariff_6month_70(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки '4490 ₽ - 6 Месяцев / 70 Каналов'"""
    await callback.answer(
        "Функция оплаты будет добавлена в ближайшее время", show_alert=True
    )


@router.message()
async def handle_channel_links(message: Message, state: FSMContext):
    """Обработчик ссылок на каналы от пользователя"""
    user = current_user.get()

    # Проверяем, пересланное ли сообщение
    if message.forward_from_chat:
        channel_links = handle_forwarded_message(message)
        if channel_links:
            # Проверяем лимит каналов
            can_add, limit_message = await check_channel_limit(
                user, len(channel_links)
            )
            if not can_add:
                await send_image_message(
                    message, "limit", limit_message, limit_reached_kb()
                )
                return

            await message.answer(
                f"Найдено каналов для добавления: {len(channel_links)}\n"
                f"Проверяем доступность и подписываемся..."
            )
            await state.update_data(channel_links=channel_links)
            await process_channel_subscription(message, state, channel_links)
            return
        else:
            await message.answer(
                "Не удалось извлечь ссылку на канал из пересланного сообщения."
            )
            return

    # Обычное сообщение
    if not message.text:
        await message.answer(
            "Отправьте текстовое сообщение со ссылками на каналы или перешлите сообщение из канала."
        )
        return

    channel_links = parse_channel_links(message.text)
    if not channel_links:
        await message.answer(
            "Не удалось найти валидные ссылки на каналы.\n\n"
            "Поддерживаемые форматы:\n"
            "• t.me/channel_name\n"
            "• https://t.me/channel_name\n"
            "• t.me/+private_link"
        )
        return

    can_add, limit_message = await check_channel_limit(user, len(channel_links))
    if not can_add:
        await send_image_message(
            message, "limit", limit_message, limit_reached_kb()
        )
        return

    await state.update_data(channel_links=channel_links)

    await message.answer(
        f"Найдено каналов для добавления: {len(channel_links)}\n"
        f"Проверяем доступность и подписываемся..."
    )

    # Обработка ссылок через userbot
    await process_channel_subscription(message, state, channel_links)


@router.callback_query(F.data.startswith("unsubscribe_"))
async def handle_unsubscribe_channel(
    callback: CallbackQuery, state: FSMContext
):
    """Хендлер отписки от канала"""
    channel_id = int(callback.data.split("_")[1])
    user = current_user.get()

    try:
        channel = await Channel.objects.aget(id=channel_id)
        await user.channels.aremove(channel)
        logger.info(
            f"Пользователь {user.tg_user_id} отписался от канала {channel.title}"
        )
        await handle_my_channels(callback, state)

    except Channel.DoesNotExist:
        logger.error(f"Канал с ID {channel_id} не найден")


def decode_ref_id(value: str) -> int | None:
    try:
        padding = "=" * (-len(value) % 4)
        return int(base64.urlsafe_b64decode(value + padding).decode())
    except Exception as e:
        logger.warning("Не удалось расшифровать ref ID", exc_info=e)
        return None


async def handle_start_referrals(message: Message, user, args: str) -> None:
    args_type, args_value = args.split("_", 1)

    now = timezone.now()
    created_delta = now - user.created
    if user.referrer_id is not None or created_delta > timedelta(minutes=1):
        return

    if args_type == "ref":
        ref_id = decode_ref_id(args_value)
        if not ref_id or ref_id == user.tg_user_id:
            return

        ref_user = await User.objects.filter(tg_user_id=ref_id).afirst()
        if ref_user:
            user.referrer = ref_user
            logger.info(
                "Пользователь приглашён по реферальной ссылке",
                invited_user_id=user.id,
                invited_tg_id=user.tg_user_id,
                invited_username=user.username,
                referrer_user_id=ref_user.id,
                referrer_tg_id=ref_user.tg_user_id,
                referrer_username=ref_user.username,
            )
            await user.asave()
        else:
            logger.warning(
                "При регистрации по реф ссылке не найден пользователь",
                ref_user=ref_user,
            )


async def process_channel_subscription(
    message: Message, state: FSMContext, channel_links: list[str]
):
    """Отправляет запрос на подписку через Redis и обрабатывает ответ"""
    user = current_user.get()

    request_id = str(uuid.uuid4())
    subscribe_request = SubscribeChannelsMessage(
        request_id=request_id,
        user_id=user.tg_user_id,
        channel_links=channel_links,
    )

    try:
        await event_manager.publish_event(
            EventType.SUBSCRIBE_CHANNELS, subscribe_request, "userbot:subscribe"
        )

        response = await event_manager.wait_for_response(request_id, timeout=60)

        if not response:
            logger.error("Таймаут ожидания ответа от userbot")
            await message.answer("Что-то пошло не так. Попробуйте еще раз.")
            return

        if not response.success:
            logger.error(
                f"Ошибка при обработке каналов: {response.error_message}"
            )
            await message.answer("Что-то пошло не так. Попробуйте еще раз.")
            return

        successful_channels = []
        failed_channels = []

        for result_data in response.results:
            result = ChannelResult(**result_data)

            if result.success and result.telegram_id and result.title:
                channel, created = await Channel.objects.aget_or_create(
                    telegram_id=result.telegram_id,
                    defaults={
                        "title": result.title,
                        "main_username": result.username,
                        "link_subscription": result.link,
                    },
                )

                if not created:
                    channel.title = result.title
                    channel.main_username = result.username
                    channel.link_subscription = result.link
                    await channel.asave()

                await sync_to_async(user.channels.add)(channel)

                if response.userbot_id > 0:

                    def create_subscription():
                        return ChannelSubscription.objects.update_or_create(
                            channel=channel,
                            userbot_id=response.userbot_id,
                            defaults={"is_subscribed": True},
                        )

                    await sync_to_async(create_subscription)()

                successful_channels.append(result.title)

                logger.info(
                    f"Пользователь {user.tg_user_id} подписался на канал {result.title}",
                    channel_created=created,
                )
            else:
                failed_channels.append(
                    f"• {result.link} - {result.error_message or 'неизвестная ошибка'}"
                )

        if len(successful_channels) == 1 and len(failed_channels) == 0:
            caption = f"Канал *{successful_channels[0]}* успешно добавлен!"
            await send_image_message(
                message=message,
                image_name="one_add",
                caption=caption,
                keyboard=back_to_menu_kb(),
            )
        elif len(successful_channels) > 1:
            caption = f"Успешно добавлено каналов: {len(successful_channels)}"
            if failed_channels:
                caption += f"\nНе удалось добавить: {len(failed_channels)}"
            await send_image_message(
                message=message,
                image_name="many_add",
                caption=caption,
                keyboard=back_to_menu_kb(),
            )
        else:
            caption = "Не удалось добавить каналы:\n" + "\n".join(
                failed_channels
            )
            await send_image_message(
                message=message,
                image_name="add_channels",
                caption=caption,
                keyboard=add_channels_kb(),
            )
            return

    except Exception as e:
        logger.error(
            f"Ошибка при обработке каналов через Redis: {e}", exc_info=True
        )
        await send_image_message(
            message=message,
            image_name="add_channels",
            caption="Произошла ошибка при добавлении каналов. Попробуйте еще раз.",
            keyboard=add_channels_kb(),
        )
        return


@router.message()
async def handle_channel_selection(message: Message, state: FSMContext):
    """Обработчик выбора канала через кнопку поиска или ввода ссылок"""
    channel_info = None

    if message.chat_shared:
        channel_info = message.chat_shared.username
        if not channel_info:
            channel_info = f"@{message.chat_shared.chat_id}"
    elif message.forward_from_chat and message.forward_from_chat.username:
        channel_info = message.forward_from_chat.username
    elif message.text:
        channel_info = message.text

    if not channel_info:
        await message.answer(
            "🌝 К сожалению, через поиск нельзя добавлять приватные каналы. Но это можно сделать вручную."
        )
        return

    try:
        channel_links = parse_channel_links(channel_info)

        if not channel_links:
            await message.answer(
                "❌ Не удалось распознать ссылки на каналы. Проверьте формат ссылок."
            )
            return

        await process_channel_subscription(message, state, channel_links)

    except Exception as e:
        logger.error(f"Ошибка при обработке канала {channel_info}: {e}")
        await message.answer(f"❌ Ошибка при добавлении канала: {e}")
