import json
from dataclasses import asdict
from dataclasses import dataclass
from enum import Enum
from typing import List
from typing import Optional

import structlog

logger = structlog.getLogger(__name__)


class MessageType(Enum):
    SUBSCRIBE_CHANNELS = "subscribe_channels"
    SUBSCRIBE_RESPONSE = "subscribe_response"
    NEW_AD_MESSAGE = "new_ad_message"


@dataclass
class NewAdMessage:
    """Сообщение о новом рекламном посте для рассылки"""

    message_type: str = MessageType.NEW_AD_MESSAGE.value
    channel_id: int = 0
    channel_title: str = ""
    message_id: int = 0
    message_text: str = ""
    channel_link: str = ""


@dataclass
class SubscribeChannelsMessage:
    """Сообщение для подписки на каналы"""

    message_type: str = MessageType.SUBSCRIBE_CHANNELS.value
    request_id: str = ""
    user_id: int = 0
    channel_links: List[str] = None

    def __post_init__(self):
        if self.channel_links is None:
            self.channel_links = []


@dataclass
class ChannelResult:
    """Результат обработки одного канала"""

    link: str
    success: bool
    telegram_id: Optional[int] = None
    title: Optional[str] = None
    username: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class SubscribeResponseMessage:
    """Ответ на запрос подписки"""

    message_type: str = MessageType.SUBSCRIBE_RESPONSE.value
    request_id: str = ""
    user_id: int = 0
    userbot_id: int = 0
    results: List[dict] = None  # List[ChannelResult as dict]
    success: bool = True
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.results is None:
            self.results = []


def serialize_message(message) -> str:
    """Сериализует сообщение в JSON"""
    try:
        return json.dumps(asdict(message), ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сериализации сообщения: {e}")
        return ""


def deserialize_message(data: str, message_class):
    """Десериализует сообщение из JSON"""
    try:
        data_dict = json.loads(data)
        return message_class(**data_dict)
    except Exception as e:
        logger.error(f"Ошибка десериализации сообщения: {e}")
        return None
