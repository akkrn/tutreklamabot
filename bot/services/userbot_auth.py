import structlog
from asgiref.sync import sync_to_async
from telethon import TelegramClient
from telethon.errors import PhoneCodeInvalidError
from telethon.errors import PhoneNumberInvalidError
from telethon.errors import SessionPasswordNeededError

from bot.models import UserBot

logger = structlog.getLogger(__name__)


class UserbotAuthService:
    """Сервис для авторизации юзерботов через веб-интерфейс"""

    @staticmethod
    async def send_code_request(userbot: UserBot) -> dict:
        """Отправляет код подтверждения на телефон"""
        try:
            client = TelegramClient(
                userbot.get_session_path(), userbot.api_id, userbot.api_hash
            )

            await client.connect()

            # Проверяем, не авторизован ли уже
            if await client.is_user_authorized():
                await client.disconnect()
                return {"success": False, "error": "Юзербот уже авторизован"}

            # Отправляем код
            sent = await client.send_code_request(userbot.phone)
            phone_code_hash = getattr(sent, "phone_code_hash", None)

            await client.disconnect()

            if phone_code_hash:
                # Обновляем статус
                await sync_to_async(userbot.save)()
                userbot.status = UserBot.STATUS_AUTHORIZING
                userbot.last_error = ""
                await sync_to_async(userbot.save)()

                return {
                    "success": True,
                    "phone_code_hash": phone_code_hash,
                    "message": f"Код отправлен на номер {userbot.phone}",
                }
            else:
                return {
                    "success": False,
                    "error": "Не удалось получить phone_code_hash",
                }

        except PhoneNumberInvalidError:
            if client.is_connected():
                await client.disconnect()
            return {"success": False, "error": "Неверный номер телефона"}
        except Exception as e:
            logger.error(f"Ошибка отправки кода для {userbot.phone}: {e}")
            if client.is_connected():
                await client.disconnect()
            userbot.status = UserBot.STATUS_ERROR
            userbot.last_error = str(e)
            await sync_to_async(userbot.save)()

            return {
                "success": False,
                "error": f"Ошибка отправки кода: {str(e)}",
            }

    @staticmethod
    async def verify_code(
        userbot: UserBot, code: str, phone_code_hash: str, password: str = None
    ) -> dict:
        """Проверяет код подтверждения и завершает авторизацию"""
        try:
            client = TelegramClient(
                userbot.get_session_path(), userbot.api_id, userbot.api_hash
            )

            await client.connect()

            # Проверяем код
            # Сначала пытаемся войти с кодом
            try:
                await client.sign_in(
                    phone=userbot.phone,
                    code=code,
                    phone_code_hash=phone_code_hash,
                )
                logger.info(f"Успешный вход с кодом для {userbot.phone}")
            except SessionPasswordNeededError:
                # Требуется пароль 2FA
                if not password:
                    await client.disconnect()
                    return {
                        "success": False,
                        "error": "Требуется пароль 2FA",
                        "needs_password": True,
                    }

                # Проверяем пароль 2FA
                logger.info(f"Проверяем пароль 2FA для {userbot.phone}")
                await client.sign_in(password=password)
                logger.info(f"Успешная проверка пароля 2FA для {userbot.phone}")
            except Exception as e:
                await client.disconnect()
                raise e

            # Проверяем авторизацию
            if await client.is_user_authorized():
                # Получаем string session для сохранения в БД
                string_session = client.session.save()

                # Обновляем статус
                userbot.status = UserBot.STATUS_ACTIVE
                userbot.is_active = True
                userbot.string_session = string_session
                userbot.last_error = ""
                await sync_to_async(userbot.save)()

                await client.disconnect()

                return {"success": True, "message": "Авторизация успешна"}
            else:
                await client.disconnect()
                return {"success": False, "error": "Авторизация не удалась"}

        except PhoneCodeInvalidError:
            await client.disconnect()
            return {"success": False, "error": "Неверный код подтверждения"}
        except Exception as e:
            logger.error(f"Ошибка проверки кода для {userbot.phone}: {e}")
            if client.is_connected():
                await client.disconnect()
            userbot.status = UserBot.STATUS_ERROR
            userbot.last_error = str(e)
            await sync_to_async(userbot.save)()

            return {"success": False, "error": f"Ошибка авторизации: {str(e)}"}

    @staticmethod
    async def check_status(userbot: UserBot) -> dict:
        """Проверяет текущий статус юзербота"""
        try:
            if userbot.string_session:
                client = TelegramClient(
                    userbot.string_session, userbot.api_id, userbot.api_hash
                )
            else:
                client = TelegramClient(
                    userbot.get_session_path(), userbot.api_id, userbot.api_hash
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
            logger.error(f"Ошибка проверки статуса для {userbot.phone}: {e}")
            return {"success": False, "error": str(e)}
