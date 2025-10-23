from aiogram.fsm.state import State, StatesGroup


class AddChannelsStates(StatesGroup):
    """Состояния для добавления каналов"""

    waiting_for_links = State()
