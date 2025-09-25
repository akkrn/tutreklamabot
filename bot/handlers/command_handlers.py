import asyncio
import base64
import structlog
from datetime import timedelta

from django.utils import timezone
from aiogram import Router
from aiogram.filters import CommandObject, CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.handlers.helpers import get_menu
from bot.keyboards import main_menu_kb
from bot.middlewares import current_user
from bot.models import RewardType, User
from bot.translations import get_translation
from bot.utils import send_video

router = Router()
logger = structlog.getLogger(__name__)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, command: CommandObject):
    await message.answer("Welcome to the bot!")
    await get_menu(message, state)