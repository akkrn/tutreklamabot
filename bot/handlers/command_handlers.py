import asyncio
import base64
import structlog
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone
from aiogram import Router, F
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.handlers.helpers import get_menu, send_image_message, generate_digest_text
from bot.keyboards import add_channels_kb, search_channels_kb, user_channels_kb, back_to_menu_kb
from bot.middlewares import current_user
from bot.models import User, Channel
from bot.translations import get_translation

router = Router()
logger = structlog.getLogger(__name__)


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
            keyboard=add_channels_kb()
        )
    else:
        await get_menu(message, state)

@router.callback_query(F.data == "add_channels_btn")
async def handle_add_channels(callback: CallbackQuery, state: FSMContext):
    await send_image_message(
        message=callback.message,
        image_name="search",
        caption="",
        keyboard=search_channels_kb(),
        edit_message=True
    ) 


@router.callback_query(F.data == "search_channels_btn")
async def handle_search_channels(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Поиск каналов'"""
    # Пока заглушка - позже здесь будет логика поиска каналов пользователя
    # TODO изменить логику на получение списка каналов пользователя
    await get_menu(callback.message, state, is_from_callback=True)


@router.callback_query(F.data == "main_menu_btn")
async def handle_main_menu(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Главное меню'"""
    await get_menu(callback.message, state, is_from_callback=True)


@router.callback_query(F.data == "my_channels_btn")
async def handle_my_channels(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Мои каналы'"""
    user = current_user.get()
    user_channels_count = await user.channels.acount()

    if user_channels_count == 0:
        caption = "У вас пока нет добавленных каналов."
        keyboard = search_channels_kb()
    else:
        caption = "Для удаления канала — нажмите на него."
        channels = await sync_to_async(list)(user.channels.all())
        keyboard = await user_channels_kb(channels)

    await send_image_message(
        message=callback.message,
        image_name="channels",
        caption=caption,
        keyboard=keyboard,
        edit_message=True
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
        edit_message=True
    )


@router.callback_query(F.data == "support_btn")
async def handle_support(callback: CallbackQuery, state: FSMContext):
    """Хендлер кнопки 'Помощь'"""
    await send_image_message(
        message=callback.message,
        image_name="support",
        caption="",
        keyboard=search_channels_kb(), 
        edit_message=True
    )


@router.callback_query(F.data.startswith("unsubscribe_"))
async def handle_unsubscribe_channel(callback: CallbackQuery, state: FSMContext):
    """Хендлер отписки от канала"""
    channel_id = int(callback.data.split("_")[1])
    user = current_user.get()

    try:
        channel = await Channel.objects.aget(id=channel_id)
        await user.channels.aremove(channel)
        logger.info(f"Пользователь {user.tg_user_id} отписался от канала {channel.title}")
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

        ref_user = await User.objects.filter(
            tg_user_id=ref_id
        ).afirst()
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

