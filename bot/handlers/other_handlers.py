from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

router = Router()


# Всегда должен идти после всех handlers
@router.message()
async def unknown(message: Message, state: FSMContext):
    """Хендлер для неизвестных сообщений"""
    await message.answer(
        "Ублюдок, мать твою, а ну, иди сюда, говно собачье, а? Сдуру решил ко мне лезть, ты? Засранец вонючий, мать твою, а? Ну, иди сюда, попробуй меня трахнуть — я тебя сам трахну, ублюдок, онанист чёртов"
    )
    # TODO Стоит "смешной" текст - заглушка, изменить на удаление некоретных сообщений
