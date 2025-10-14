import structlog
from asgiref.sync import sync_to_async
from telethon import TelegramClient
from telethon.sessions import StringSession

from bot.models import UserBot

logger = structlog.getLogger(__name__)


class UserbotManagerService:
    """Сервис для управления юзерботами через админку"""

    @staticmethod
    async def start_userbot(userbot: UserBot) -> dict:
        """Запускает юзербота"""
        try:
            if not userbot.is_active:
                return {"success": False, "error": "Юзербот не авторизован"}

            # Проверяем соединение
            if userbot.string_session:
                client = TelegramClient(
                    StringSession(userbot.string_session),
                    userbot.api_id,
                    userbot.api_hash,
                )
            else:
                client = TelegramClient(
                    userbot.get_session_path(), userbot.api_id, userbot.api_hash
                )

            await client.connect()

            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()

                # Обновляем статус
                userbot.status = UserBot.STATUS_ACTIVE
                userbot.last_error = ""
                await sync_to_async(userbot.save)()

                return {
                    "success": True,
                    "message": f"Юзербот {userbot.name} запущен успешно",
                    "user_info": {
                        "id": me.id,
                        "username": me.username,
                        "first_name": me.first_name,
                    },
                }
            else:
                await client.disconnect()
                return {"success": False, "error": "Юзербот не авторизован"}

        except Exception as e:
            logger.error(f"Ошибка запуска юзербота {userbot.name}: {e}")
            userbot.status = UserBot.STATUS_ERROR
            userbot.last_error = str(e)
            await sync_to_async(userbot.save)()

            return {"success": False, "error": f"Ошибка запуска: {str(e)}"}

    @staticmethod
    async def stop_userbot(userbot: UserBot) -> dict:
        """Останавливает юзербота"""
        try:
            userbot.status = UserBot.STATUS_INACTIVE
            userbot.last_error = ""
            await sync_to_async(userbot.save)()

            return {
                "success": True,
                "message": f"Юзербот {userbot.name} остановлен",
            }

        except Exception as e:
            logger.error(f"Ошибка остановки юзербота {userbot.name}: {e}")
            return {"success": False, "error": f"Ошибка остановки: {str(e)}"}

    @staticmethod
    async def restart_userbot(userbot: UserBot) -> dict:
        """Перезапускает юзербота"""
        try:
            # Сначала останавливаем
            stop_result = await UserbotManagerService.stop_userbot(userbot)
            if not stop_result["success"]:
                return stop_result

            # Затем запускаем
            start_result = await UserbotManagerService.start_userbot(userbot)
            return start_result

        except Exception as e:
            logger.error(f"Ошибка перезапуска юзербота {userbot.name}: {e}")
            return {"success": False, "error": f"Ошибка перезапуска: {str(e)}"}

    @staticmethod
    async def check_userbot_status(userbot: UserBot) -> dict:
        """Проверяет статус юзербота"""
        try:
            if not userbot.string_session:
                return {"success": False, "error": "Нет сохраненной сессии"}

            client = TelegramClient(
                StringSession(userbot.string_session),
                userbot.api_id,
                userbot.api_hash,
            )

            await client.connect()

            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()

                return {
                    "success": True,
                    "authorized": True,
                    "user_info": {
                        "id": me.id,
                        "username": me.username,
                        "first_name": me.first_name,
                        "last_name": me.last_name,
                    },
                }
            else:
                await client.disconnect()
                return {"success": True, "authorized": False}

        except Exception as e:
            logger.error(
                f"Ошибка проверки статуса юзербота {userbot.name}: {e}"
            )
            return {"success": False, "error": str(e)}
