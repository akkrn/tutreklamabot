from django.utils import timezone
from aiogram import Dispatcher, Router
from aiogram.filters.chat_member_updated import KICKED, MEMBER, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

from bot.middlewares import current_user
from bot.models import User

router = Router()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED))
async def user_blocked_bot(event: ChatMemberUpdated, dispatcher: Dispatcher):
    user = current_user.get(None)
    user.status = User.STATUS_BANNED
    user.status_changed_at = timezone.now()
    await user.asave(update_fields=["status", "status_changed_at"])
    key = StorageKey(
        bot_id=event.bot.id,
        chat_id=event.chat.id,
        user_id=event.from_user.id,
    )
    state = FSMContext(storage=dispatcher.fsm.storage, key=key)
    await state.clear()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER))
async def user_unblocked_bot(event: ChatMemberUpdated):
    user = current_user.get(None)
    user.status = User.STATUS_ACTIVE
    user.status_changed_at = timezone.now()
    await user.asave(update_fields=["status", "status_changed_at"])
