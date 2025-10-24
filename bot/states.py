from aiogram.fsm.state import State, StatesGroup


class AddChannelsStates(StatesGroup):
    """Состояния для добавления каналов"""

    waiting_for_links = State()


class DigestStates(StatesGroup):
    """Состояния для пагинации дайджеста"""

    viewing_digest = State()


class ChannelsStates(StatesGroup):
    """Состояния для пагинации моих каналов"""

    viewing_channels = State()
