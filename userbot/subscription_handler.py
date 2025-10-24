"""Обработчик подписок на каналы"""

from typing import TYPE_CHECKING

import structlog
from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from bot.models import Channel, ChannelSubscription
from core.event_manager import EventType, event_manager
from userbot.redis_messages import (
    SubscribeChannelsMessage,
    SubscribeResponseMessage,
)

if TYPE_CHECKING:
    from userbot.core import UserbotCore

logger = structlog.getLogger(__name__)


class SubscriptionHandler:
    """Обработчик подписок на каналы"""

    def __init__(self, userbot_core: "UserbotCore"):
        self.userbot_core = userbot_core

    async def handle_subscribe_request(self, request: SubscribeChannelsMessage):
        """Обрабатывает запрос на подписку от бота"""
        logger.info(f"Получен запрос подписки: {request.request_id}")

        results = []
        for channel_link in request.channel_links:
            result = await self._subscribe_to_channel(channel_link)
            results.append(result)

        response = SubscribeResponseMessage(
            request_id=request.request_id,
            user_id=request.user_id,
            results=results,
        )
        await event_manager.publish_event(
            EventType.SUBSCRIBE_RESPONSE,
            response,
            f"bot:response:{request.request_id}",
        )

    async def _subscribe_to_channel(self, channel_link: str) -> dict:
        """Подписывается на канал"""
        try:
            userbot = await self.userbot_core._select_best_userbot()
            if not userbot:
                return {
                    "link": channel_link,
                    "success": False,
                    "telegram_id": None,
                    "title": None,
                    "username": None,
                    "error_message": "Нет доступных юзерботов",
                }

            client = self.userbot_core.get_client(userbot.id)
            if not client:
                return {
                    "link": channel_link,
                    "success": False,
                    "telegram_id": None,
                    "title": None,
                    "username": None,
                    "error_message": "Клиент юзербота недоступен",
                }

            result = await self._perform_subscription(client, channel_link)

            if result["success"]:
                await self._create_or_update_channel(result, userbot)
            else:
                logger.error(
                    f"Ошибка подписки на канал {channel_link}: {result['error_message']}"
                )

            return result

        except Exception as e:
            logger.error(f"Ошибка подписки на канал {channel_link}: {e}")
            return {
                "link": channel_link,
                "success": False,
                "telegram_id": None,
                "title": None,
                "username": None,
                "error_message": str(e),
            }

    async def _perform_subscription(self, client, channel_link: str) -> dict:
        """Выполняет подписку на канал через Telegram API"""
        try:
            if "t.me/+" in channel_link:
                invite_hash = channel_link.split("t.me/+", 1)[1]
                try:
                    updates = await client(ImportChatInviteRequest(invite_hash))
                    if hasattr(updates, "chats") and updates.chats:
                        entity = updates.chats[0]
                    else:
                        raise Exception(
                            "Не удалось получить информацию о канале после подписки"
                        )
                except Exception as e:
                    raise Exception(
                        f"Ошибка подписки по инвайт-ссылке: {str(e)}"
                    )

                result = {
                    "link": channel_link,
                    "success": True,
                    "telegram_id": abs(entity.id),
                    "title": entity.title,
                    "username": getattr(entity, "username", None),
                    "error_message": None,
                }
            else:
                entity = await client.get_entity(channel_link)
                await client(JoinChannelRequest(entity))

                result = {
                    "link": channel_link,
                    "success": True,
                    "telegram_id": abs(entity.id),
                    "title": entity.title,
                    "username": getattr(entity, "username", None),
                    "error_message": None,
                }

            return result

        except UserAlreadyParticipantError:
            try:
                entity = await client.get_entity(channel_link)
                return {
                    "link": channel_link,
                    "success": True,
                    "telegram_id": abs(entity.id),
                    "title": entity.title,
                    "username": getattr(entity, "username", None),
                    "error_message": None,
                }
            except Exception as e:
                return {
                    "link": channel_link,
                    "success": False,
                    "telegram_id": None,
                    "title": None,
                    "username": None,
                    "error_message": f"Ошибка получения информации о канале: {str(e)}",
                }

        except Exception as e:
            return {
                "link": channel_link,
                "success": False,
                "telegram_id": None,
                "title": None,
                "username": None,
                "error_message": str(e),
            }

    async def _create_or_update_channel(self, result: dict, userbot):
        """Создает или обновляет запись канала в БД"""
        try:
            is_private = not result["username"]

            channel, created = await Channel.objects.aget_or_create(
                telegram_id=result["telegram_id"],
                defaults={
                    "title": result["title"],
                    "main_username": result["username"],
                    "link_subscription": result["link"],
                    "is_private": is_private,
                },
            )

            if not created:
                channel.title = result["title"]
                channel.main_username = result["username"]
                channel.link_subscription = result["link"]
                channel.is_private = is_private
                await channel.asave()

            # Создаем подписку
            (
                subscription,
                created,
            ) = await ChannelSubscription.objects.aget_or_create(
                channel=channel,
                userbot=userbot,
                defaults={"is_subscribed": True},
            )

            if not created:
                subscription.is_subscribed = True
                await subscription.asave()

            logger.info(f"Канал {channel.title} добавлен/обновлен")

        except Exception as e:
            logger.error(f"Ошибка создания записи канала: {e}")
