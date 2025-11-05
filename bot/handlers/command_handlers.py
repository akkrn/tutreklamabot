import base64
import uuid
from datetime import timedelta

import structlog
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.handlers.helpers import (
    generate_digest_text_paginated,
    get_menu,
    send_file_message,
)
from bot.keyboards import (
    add_channels_kb,
    add_more_channels_kb,
    back_to_menu_kb,
    cancel_reccurent_kb,
    digest_kb,
    limit_reached_kb,
    new_menu_kb,
    support_kb,
    tariff_kb,
    user_channels_kb,
)
from bot.middlewares import current_user
from bot.models import Channel, ChannelSubscription, User
from bot.services.payment_service import cancel_recurring
from bot.states import AddChannelsStates, ChannelsStates, DigestStates
from bot.utils.link_parser import handle_forwarded_message, parse_channel_links
from core.event_manager import EventType, event_manager
from userbot.redis_messages import ChannelResult, SubscribeChannelsMessage

router = Router()
logger = structlog.getLogger(__name__)

MAX_CHANNELS_PER_USER = 7


async def check_channel_limit(
    user: User, new_channels_count: int
) -> tuple[bool, str]:
    """Проверяет, не превышает ли пользователь лимит каналов"""
    current_channels_count = await sync_to_async(
        lambda: user.subscribed_channels_count
    )()

    # Получаем лимит каналов для текущего тарифа пользователя
    channels_limit = await sync_to_async(user.get_channels_limit)()

    total_channels = current_channels_count + new_channels_count

    if total_channels > channels_limit:
        remaining_slots = channels_limit - current_channels_count
        if remaining_slots <= 0:
            return (
                False,
                f"<b>Достигнут лимит запросов в вашем тарифе.</b> Пожалуйста, смените тариф.\n\nКаналов добавлено: {current_channels_count}/{channels_limit}",
            )
        else:
            return (
                False,
                f"Количество каналов, которые вы можете добавить: {remaining_slots}.\n\nУ вас уже {current_channels_count} из {channels_limit}.",
            )

    return True, ""


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, command: CommandObject):
    user = current_user.get()
    await state.clear()
    if command.args:
        await handle_start_referrals(message, user, command.args)

    await send_file_message(
        message=message,
        file_name="add_channels.jpg",
        caption="",
        keyboard=add_channels_kb(),
    )


@router.message(Command("menu"))
async def menu_command(message: Message, state: FSMContext):
    """Хендлер команды /menu - показывает главное меню"""
    await get_menu(message, state)


@router.callback_query(F.data == "add_channels_btn")
async def handle_add_channels(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Добавить канал' - устанавливает состояние ожидания ссылок"""
    await state.set_state(AddChannelsStates.waiting_for_links)
    await send_file_message(
        message=callback.message,
        file_name="search.jpg",
        caption="Отправьте одну или несколько ссылок через пробел и бот начнёт отслеживать рекламные посты в этих каналах.",
        keyboard=back_to_menu_kb(),
        edit_message=True,
    )


@router.callback_query(F.data == "main_menu_btn")
async def handle_main_menu(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Главное меню'"""
    await get_menu(callback.message, state, is_from_callback=True)


@router.callback_query(F.data == "new_main_menu_btn")
async def handle_new_main_menu(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Главное меню'"""
    await callback.message.edit_reply_markup(reply_markup=None)
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
        caption = "<b>Для удаления канала</b> — нажмите на него."
        channels = await sync_to_async(list)(user.channels.all())
        keyboard = await user_channels_kb(channels, page=0)

        await state.set_state(ChannelsStates.viewing_channels)
        await state.update_data(
            channels_page=0, user_channels_ids=[ch.id for ch in channels]
        )

    await send_file_message(
        message=callback.message,
        file_name="channels.jpg",
        caption=caption,
        keyboard=keyboard,
        edit_message=True,
    )


@router.callback_query(
    F.data.startswith("channels_page_"),
    StateFilter(ChannelsStates.viewing_channels),
)
async def handle_channels_pagination(
    callback: CallbackQuery, state: FSMContext
):
    """Обработчик пагинации для моих каналов"""
    page = int(callback.data.split("_")[-1])
    data = await state.get_data()
    user_channels_ids = data.get("user_channels_ids", [])

    user_channels = []
    channels = await sync_to_async(list)(
        Channel.objects.filter(id__in=user_channels_ids)
    )
    user_channels.extend(channels)

    keyboard = await user_channels_kb(user_channels, page=page)

    await state.update_data(channels_page=page)

    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data == "digest_btn")
async def handle_digest(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Дайджест'"""
    callback.answer()

    digest_caption, total_pages = await generate_digest_text_paginated(page=0)

    await state.set_state(DigestStates.viewing_digest)
    await state.update_data(digest_page=0, digest_total_pages=total_pages)

    keyboard = digest_kb(page=0, total_pages=total_pages)

    await send_file_message(
        message=callback.message,
        file_name="digest.jpg",
        caption=digest_caption,
        keyboard=keyboard,
        edit_message=True,
    )


@router.callback_query(
    F.data.startswith("digest_page_"), StateFilter(DigestStates.viewing_digest)
)
async def handle_digest_pagination(callback: CallbackQuery, state: FSMContext):
    """Обработчик пагинации для дайджеста"""
    try:
        page = int(callback.data.split("_")[-1])

        digest_caption, total_pages = await generate_digest_text_paginated(
            page=page
        )

        await state.update_data(
            digest_page=page, digest_total_pages=total_pages
        )

        keyboard = digest_kb(page=page, total_pages=total_pages)

        await callback.message.edit_caption(
            caption=digest_caption,
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(
            f"Ошибка при обработке пагинации дайджеста: {e}", exc_info=True
        )


@router.callback_query(F.data == "support_btn")
async def handle_support(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Помощь'"""
    support_text = """<b>Как это работает?</b>
Бот отслеживает телеграм-каналы и присылает рекламные посты. Вы видите, кто размещается у конкурентов, и можете предложить рекламу у себя.

<b>Как связаться с рекламодателем?</b>
· Если рекламируют канал → контакты в описании;
· Сайт → ищите почту или соцсети;
· Нет контактов → спросите у админа канала.

<b>Что писать рекламодателю?</b>
· Опишите свою аудиторию;
· Дайте цифры и статистику;
· Покажите, чем вы лучше конкурентов."""

    await send_file_message(
        message=callback.message,
        file_name="video.mp4",
        caption=support_text,
        keyboard=support_kb(),
        edit_message=True,
    )


@router.callback_query(F.data == "change_tariff_btn")
async def handle_change_tariff(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Сменить тариф'"""
    tariff_text = (
        "Оплачивая, вы принимаете <a href='https://telegra.ph/Publichnaya-oferta-o-zaklyuchenii-dogovora-informacionno-konsultacionnyh-uslug-10-21'>публичную оферту</a> "
        "и соглашение о присоединении к <a href='https://telegra.ph/Soglashenie-o-prisoedinenii-k-rekurrentnoj-sisteme-platezhej-10-21'>рекуррентной системе</a> платежей.\n\n"
        "Перед оплатой рекомендуем отключить VPN."
    )
    message_id = callback.message.message_id if callback.message else None
    keyboard = await tariff_kb(message_id=message_id)

    await send_file_message(
        message=callback.message,
        file_name="payment.jpg",
        caption=tariff_text,
        keyboard=keyboard,
        edit_message=True,
    )


@router.callback_query(F.data == "cancel_reccurent_btn")
async def handle_cancel_reccurent(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Отменить подписку'"""
    cancel_reccurent_text = (
        "❤️ <b>Внимание!</b> При отключении — тариф сохранится до конца оплаченного периода.\n\n"
        "Отключаем?"
    )

    await send_file_message(
        message=callback.message,
        file_name="cancel_subscription.jpg",
        caption=cancel_reccurent_text,
        keyboard=cancel_reccurent_kb(),
        edit_message=True,
    )


@router.callback_query(F.data == "cancel_reccurent_done_btn")
async def handle_cancel_reccurent_done(
    callback: CallbackQuery, state: FSMContext
):
    """Хендлер кнопки 'Отключить автоплатеж' и подтверждения"""
    user = current_user.get()
    await cancel_recurring(user)
    cancel_reccurent_text = (
        "Автоплатеж отключен. Спасибо за использование нашего сервиса!"
    )

    await send_file_message(
        message=callback.message,
        file_name="cancel_subscription_done.jpg",
        caption=cancel_reccurent_text,
        keyboard=new_menu_kb(),
        edit_message=True,
    )


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
        data = await state.get_data()
        current_page = data.get("channels_page", 0)
        user_channels_ids = data.get("user_channels_ids", [])

        if channel_id in user_channels_ids:
            user_channels_ids.remove(channel_id)

        user_channels = []
        for ch_id in user_channels_ids:
            try:
                ch = await Channel.objects.aget(id=ch_id)
                user_channels.append(ch)
            except Channel.DoesNotExist:
                continue

        channels_per_page = 10
        total_pages = (
            len(user_channels) + channels_per_page - 1
        ) // channels_per_page
        if total_pages > 0 and current_page >= total_pages:
            current_page = total_pages - 1

        await state.update_data(
            channels_page=current_page, user_channels_ids=user_channels_ids
        )

        keyboard = await user_channels_kb(user_channels, page=current_page)
        await callback.message.edit_reply_markup(reply_markup=keyboard)

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

    status_message = None
    try:
        await event_manager.publish_event(
            EventType.SUBSCRIBE_CHANNELS, subscribe_request, "userbot:subscribe"
        )

        # Отправляем системное сообщение "Добавляю каналы..."
        status_message = await message.answer(
            "Добавляю каналы...",
            parse_mode=ParseMode.HTML,
        )

        response = await event_manager.wait_for_response(
            request_id, timeout=100
        )

        if not response or not response.success:
            await send_file_message(
                message=status_message,
                file_name="error.jpg",
                caption="Что-то пошло не так. Попробуйте еще раз.",
                keyboard=back_to_menu_kb(),
                edit_message=True,
            )
            return

        successful_channels = []
        failed_channels = []

        for result_data in response.results:
            result = ChannelResult(**result_data)

            if result.success and result.telegram_id and result.title:
                # Определяем, является ли канал приватным
                is_private = (
                    not result.username
                )  # Если нет username, то канал приватный

                channel, created = await Channel.objects.aget_or_create(
                    telegram_id=result.telegram_id,
                    defaults={
                        "title": result.title,
                        "main_username": result.username,
                        "link_subscription": result.link,
                        "is_private": is_private,
                    },
                )

                if not created:
                    channel.title = result.title
                    channel.main_username = result.username
                    channel.link_subscription = result.link
                    channel.is_private = is_private
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
                failed_channels.append(f"• {result.link}")

        def get_user_info():
            user = current_user.get()
            current_subscription = user.get_subscription_info()
            channels_limit = current_subscription.get("channels_limit")
            channels_count = user.subscribed_channels_count
            return channels_limit, channels_count

        channels_limit, channels_count = await sync_to_async(get_user_info)()
        if len(successful_channels) == 1 and len(failed_channels) == 0:
            caption = f"""Канал <b>{successful_channels[0]}</b> успешно добавлен!

Каналов добавлено: {channels_count}/{channels_limit}"""
            await send_file_message(
                message=status_message,
                file_name="one_add.jpg",
                caption=caption,
                keyboard=add_more_channels_kb(),
                edit_message=True,
            )
            return True
        elif len(successful_channels) > 1 and len(failed_channels) == 0:
            caption = f"""<b>Чудесно!</b> ✨ Теперь вы будете получать уведомления о рекламе из этих каналов.

Каналов добавлено: {channels_count}/{channels_limit}"""
            await send_file_message(
                message=status_message,
                file_name="many_add.jpg",
                caption=caption,
                keyboard=add_more_channels_kb(),
                edit_message=True,
            )
            return True
        elif len(successful_channels) > 0:
            caption = f"""<b>Где-то допущена ошибка.</b>

Каналов добавлено: {channels_count}/{channels_limit}, кроме:\n{'\n'.join(failed_channels)}"""
            await send_file_message(
                message=status_message,
                file_name="almost.jpg",
                caption=caption,
                keyboard=add_more_channels_kb(),
                edit_message=True,
            )
            return True
        else:
            caption = "<b>Каналы не найдены.</b> Возможно, вы пропустили пробелы между ссылками."
            await send_file_message(
                message=status_message,
                file_name="error.jpg",
                caption=caption,
                keyboard=back_to_menu_kb(),
                edit_message=True,
            )
            return False

    except Exception as e:
        logger.error(
            f"Ошибка при обработке каналов через Redis: {e}", exc_info=True
        )
        # Если status_message был создан, редактируем его, иначе отправляем новое
        try:
            if status_message:
                await send_file_message(
                    message=status_message,
                    file_name="error.jpg",
                    caption="Произошла ошибка при добавлении каналов. Попробуйте еще раз.",
                    keyboard=back_to_menu_kb(),
                    edit_message=True,
                )
            else:
                await send_file_message(
                    message=message,
                    file_name="error.jpg",
                    caption="Произошла ошибка при добавлении каналов. Попробуйте еще раз.",
                    keyboard=back_to_menu_kb(),
                )
        except Exception:
            await message.answer("Что-то пошло не так. Попробуйте еще раз.")
        return False


@router.message(Command("remove"))
async def cmd_remove(message: Message, state: FSMContext):
    await message.answer(
        text="Клавиатура удалена", reply_markup=ReplyKeyboardRemove()
    )


@router.callback_query(F.data == "noop")
async def handle_noop(callback: CallbackQuery, state: FSMContext):
    """Обработчик для кнопок, которые не должны ничего делать"""
    callback.answer()


@router.message()
async def handle_channel_links(message: Message, state: FSMContext):
    """Обработчик ссылок на каналы от пользователя"""
    user = current_user.get()

    # Для медиа-групп обрабатываем только сообщения с текстом/подписью
    if message.media_group_id and not message.text and not message.caption:
        return  # Пропускаем сообщения без текста в медиа-группе

    if message.forward_from_chat:
        channel_links = handle_forwarded_message(message)
        if channel_links:
            can_add, limit_message = await check_channel_limit(
                user, len(channel_links)
            )
            if not can_add:
                await send_file_message(
                    message=message,
                    file_name="limit.jpg",
                    caption=limit_message,
                    keyboard=limit_reached_kb(),
                )
                return
            await state.update_data(channel_links=channel_links)
            await process_channel_subscription(message, state, channel_links)
            await state.clear()
            return

    current_state = await state.get_state()
    if current_state != AddChannelsStates.waiting_for_links:
        return

    if not message.text:
        return

    channel_links = parse_channel_links(message.text)
    if not channel_links:
        return

    can_add, limit_message = await check_channel_limit(user, len(channel_links))
    if not can_add:
        await send_file_message(
            message=message,
            file_name="limit.jpg",
            caption=limit_message,
            keyboard=limit_reached_kb(),
        )
        return

    await state.update_data(channel_links=channel_links)
    result = await process_channel_subscription(message, state, channel_links)
    if result:
        await state.clear()
