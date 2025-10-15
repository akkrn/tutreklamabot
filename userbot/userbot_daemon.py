import asyncio
from typing import Optional

import structlog
from asgiref.sync import sync_to_async
from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from bot.models import Channel, ChannelNews
from core.event_manager import EventType, event_manager
from userbot.redis_messages import (
    ChannelResult,
    NewAdMessage,
    SubscribeChannelsMessage,
    SubscribeResponseMessage,
)

logger = structlog.getLogger(__name__)


class UserbotDaemon:
    def __init__(self, userbot):
        self.userbot = userbot
        self.client = None
        self.running = False

    async def start(self):
        """Запускает userbot daemon"""
        try:
            # Инициализируем клиент с данными из UserBot
            api_id = self.userbot.api_id
            api_hash = self.userbot.api_hash
            string_session = self.userbot.string_session

            if string_session:
                self.client = TelegramClient(
                    StringSession(string_session), api_id, api_hash
                )
            else:
                # Используем путь к файлу сессии
                session_path = self.userbot.get_session_path()
                self.client = TelegramClient(session_path, api_id, api_hash)

            await self.client.connect()

            if not await self.client.is_user_authorized():
                logger.error("Userbot не авторизован. Запустите авторизацию.")
                return

            # Регистрируем обработчик запросов подписки
            event_manager.register_handler(
                EventType.SUBSCRIBE_CHANNELS,
                self.handle_subscribe_request,
                "userbot:subscribe",
            )

            # Запускаем менеджер событий
            await event_manager.start_listening()

            # Регистрируем обработчики событий Telegram
            self.client.add_event_handler(
                self.handle_new_message, events.NewMessage(incoming=True)
            )

            logger.info("Userbot daemon запущен и слушает сообщения")
            self.running = True

            # Запускаем клиент
            await self.client.run_until_disconnected()

        except Exception as e:
            logger.error(
                f"Ошибка при запуске userbot daemon: {e}", exc_info=True
            )
        finally:
            self.running = False
            await event_manager.stop_listening()
            if self.client:
                await self.client.disconnect()

    async def handle_subscribe_request(self, request: SubscribeChannelsMessage):
        """Обрабатывает запрос на подписку от бота"""
        logger.info(f"Получен запрос подписки: {request.request_id}")

        results = []

        for link in request.channel_links:
            try:
                # Определяем тип ссылки и подписываемся
                if link.startswith("https://t.me/+"):
                    # Инвайт-ссылка для приватных каналов/групп
                    invite_hash = link.replace("https://t.me/+", "")
                    try:
                        updates = await self.client(
                            ImportChatInviteRequest(invite_hash)
                        )
                        # Получаем информацию о канале после успешной подписки
                        if hasattr(updates, "chats") and updates.chats:
                            entity = updates.chats[0]
                        else:
                            raise Exception(
                                "Не удалось получить информацию о канале после подписки"
                            )
                    except Exception as e:
                        result = ChannelResult(
                            link=link,
                            success=False,
                            error_message=f"Ошибка подписки по инвайт-ссылке: {str(e)}",
                        )
                        results.append(result)
                        continue
                else:
                    # Обычная ссылка на публичный канал
                    try:
                        entity = await self.client.get_entity(link)
                    except Exception as e:
                        result = ChannelResult(
                            link=link,
                            success=False,
                            error_message=f"Не удалось получить информацию о канале: {str(e)}",
                        )
                        results.append(result)
                        continue

                # Подписываемся на канал (если это не инвайт-ссылка)
                if not link.startswith("https://t.me/+"):
                    try:
                        await self.client(JoinChannelRequest(entity))
                    except UserAlreadyParticipantError:
                        pass  # Уже подписан
                    except Exception as e:
                        result = ChannelResult(
                            link=link,
                            success=False,
                            error_message=f"Ошибка подписки: {str(e)}",
                        )
                        results.append(result)
                        continue

                result = ChannelResult(
                    link=link,
                    success=True,
                    telegram_id=abs(entity.id),
                    title=entity.title,
                    username=getattr(entity, "username", None),
                )

                logger.info(f"Успешно подписался на канал: {entity.title}")

            except Exception as e:
                result = ChannelResult(
                    link=link,
                    success=False,
                    error_message=f"Общая ошибка: {str(e)}",
                )
                logger.error(f"Ошибка при обработке канала {link}: {e}")

            results.append(result)

        # Отправляем ответ
        response = SubscribeResponseMessage(
            request_id=request.request_id,
            user_id=request.user_id,
            results=[result.__dict__ for result in results],
            success=True,
        )

        await event_manager.publish_event(
            EventType.SUBSCRIBE_RESPONSE,
            response,
            f"bot:response:{request.request_id}",
        )
        logger.info(f"Отправлен ответ на запрос: {request.request_id}")

    async def handle_new_message(self, event):
        """Обработчик новых сообщений из каналов"""
        try:
            if not event.is_channel:
                return

            message = event.message
            chat = await event.get_chat()
            channel = await self.get_channel_by_id(
                chat.id
            )  # TODO кэшировать каналы
            if not channel:
                return
            if not await self.is_advertisement(message):
                return
            await self.save_channel_news(channel, message, chat)

            logger.info(
                f"Сохранено рекламное сообщение из канала {chat.title}",
                channel_id=chat.id,
                message_id=message.id,
            )

        except Exception as e:
            logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)

    @sync_to_async
    def get_channel_by_id(self, channel_id: int) -> Optional[Channel]:
        """Получает канал из БД по ID"""
        try:
            return Channel.objects.get(telegram_id=abs(channel_id))
        except Channel.DoesNotExist:
            return None

    async def is_advertisement(self, message) -> bool:
        """Определяет является ли сообщение рекламным"""
        if not message.text:
            return False

        text = message.text.lower()

        # Проверяем обязательные маркеры
        required_markers = ["erid", "инн", "интеграция"]
        has_required_marker = any(marker in text for marker in required_markers)

        if not has_required_marker:
            return False

        # Список рекламных слов
        ad_words = [
            "авторский",
            "авторского",
            "авторская",
            "авторской",
            "авторские",
            "авторских",
            "автор",
            "автора",
            "авторы",
            "акция",
            "акции",
            "актуально",
            "актуальный",
            "актуальная",
            "актуальные",
            "актуальной",
            "актуальным",
            "бесплатно",
            "бесплатный",
            "бесплатом",
            "бесплатных",
            "бесплатные",
            "бизнес",
            "бизнесу",
            "бизнеса",
            "бизнесе",
            "бизнесмен",
            "бизнесменом",
            "бизнесмена",
            "бизнесмены",
            "бот",
            "боте",
            "боту",
            "бота",
            "бонус",
            "бонусы",
            "бонусов",
            "бонусный",
            "бронируй",
            "бронируйте",
            "вебинар",
            "вебинара",
            "вебинары",
            "войти",
            "входи",
            "вход",
            "входа",
            "врывайся",
            "врывайтесь",
            "воспользоваться",
            "воспользуйся",
            "воспользуйтесь",
            "выигрывай",
            "выиграй",
            "выигрывайте",
            "выигрывать",
            "гарантия",
            "гарантию",
            "гарантии",
            "гарантируем",
            "гарантированный",
            "гарантированная",
            "горбатиться",
            "горбатятся",
            "доход",
            "дохода",
            "доходов",
            "доступ",
            "доступа",
            "доступный",
            "доступные",
            "доступная",
            "доступного",
            "доступных",
            "ждет",
            "ждут",
            "ждите",
            "жми",
            "закреп",
            "закрепе",
            "закрепа",
            "забирай",
            "забираем",
            "забирайте",
            "заработать",
            "зарабатывать",
            "зарабатывает",
            "заработок",
            "заработка",
            "заработки",
            "зарегистрируй",
            "зарегистрируйся",
            "зарегистрироваться",
            "забрать",
            "забери",
            "заберите",
            "запуск",
            "запуска",
            "запуском",
            "запуски",
            "запустил",
            "запустила",
            "запустили",
            "запускаешь",
            "запускай",
            "заказ",
            "заказы",
            "заказа",
            "заказе",
            "заказывай",
            "заказывайте",
            "заказывать",
            "закажи",
            "закрыт",
            "закрытый",
            "закрытая",
            "закрытое",
            "закрытым",
            "закрытой",
            "закрыли",
            "закроем",
            "заявка",
            "заявку",
            "заявки",
            "заявкам",
            "запись",
            "записывай",
            "запиши",
            "запишись",
            "записаться",
            "записывайся",
            "здесь",
            "изжил",
            "изжила",
            "интенсив",
            "интенсиве",
            "интенсива",
            "канал",
            "канале",
            "каналов",
            "каналы",
            "конкурсе",
            "конкурс",
            "курс",
            "курса",
            "курсы",
            "купи",
            "купите",
            "криптомир",
            "криптомире",
            "криптоинвестиций",
            "криптоинвестиция",
            "кешбек",
            "кешбэк",
            "кешбэка",
            "кешбэком",
            "мастер-класс",
            "мастер-классы",
            "мастер-классе",
            "марафон",
            "марафоне",
            "моментально",
            "моментальный",
            "моментальная",
            "моментальные",
            "моментальных",
            "монетизация",
            "монетизации",
            "монетизировать",
            "монетизируй",
            "мини-курс",
            "мини-курса",
            "мини-курсе",
            "мини-курсы",
            "миллион",
            "миллиона",
            "миллионов",
            "миллионы",
            "миллиард",
            "миллиарда",
            "миллиардов",
            "миллиарды",
            "можете",
            "можешь",
            "нажать",
            "нажми",
            "нажмите",
            "начать",
            "начинать",
            "начни",
            "ниша",
            "нише",
            "ниши",
            "нищим",
            "нищими",
            "нищеброд",
            "нищебродов",
            "ноль",
            "нуля",
            "нулем",
            "научим",
            "научат",
            "научу",
            "научитесь",
            "образование",
            "образования",
            "онлайн",
            "освой",
            "освойте",
            "осваивать",
            "освоить",
            "оставить",
            "оставь",
            "оставлять",
            "открыть",
            "открыто",
            "открыт",
            "открыта",
            "открывается",
            "инвестиции",
            "инвестиция",
            "инвестируй",
            "интенсив",
            "интенсива",
            "интенисиве",
            "интенсивный",
            "подготовил",
            "подготовили",
            "переходи",
            "пройди",
            "проходи",
            "пройдите",
            "пройдем",
            "пройдём",
            "перейди",
            "перейти",
            "переходите",
            "покупай",
            "покупайте",
            "покажет",
            "покажут",
            "погрузись",
            "погрузитесь",
            "погружайтесь",
            "подписывайся",
            "подписывайтесь",
            "подписаться",
            "подпишись",
            "подпишитесь",
            "подпишитесь на",
            "подписка",
            "подписчиков",
            "подписчикам",
            "подписчиков",
            "подробности",
            "подробностей",
            "подробнее",
            "понадобится",
            "посмотреть",
            "прибыль",
            "прибыльный",
            "прибыльную",
            "присоединиться",
            "присоединяйтесь",
            "присоединяйся",
            "приватный",
            "приватная",
            "приватное",
            "приз",
            "приза",
            "призе",
            "призы",
            "призов",
            "приходи",
            "приходите",
            "продажи",
            "продать",
            "продающий",
            "продающая",
            "продащего",
            "продающие",
            "подработку",
            "подработки",
            "подработке",
            "подработка",
            "протестировать",
            "протестировал",
            "прокачай",
            "прокачаем",
            "прокачаете",
            "прокачают",
            "промокод",
            "промокоды",
            "промокодах",
            "промокоду",
            "расскажут",
            "расскажет",
            "рассказывает",
            "рассказывают",
            "регистрация",
            "регистрацию",
            "регистрируйся",
            "реклама",
            "рекламу",
            "рекламе",
            "рекламодатель",
            "рекламодателю",
            "рекламный",
            "рекламная",
            "рекламные",
            "рекламного",
            "рекламных",
            "раздачу",
            "раздаем",
            "раздаём",
            "раздадим",
            "рублей",
            "раздают",
            "создал",
            "создала",
            "создали",
            "создавать",
            "создать",
            "скачать",
            "скачай",
            "скачивать",
            "скачивай",
            "скачивайте",
            "скидку",
            "скидка",
            "скидкой",
            "смотреть",
            "смотрите",
            "собрали",
            "собрал",
            "собирали",
            "собрал",
            "становиться",
            "стал",
            "стать",
            "станешь",
            "становишься",
            "спешите",
            "спеши",
            "сервис",
            "сервиса",
            "сервисом",
            "ссылку",
            "ссылка",
            "ссылки",
            "ссылке",
            "ссылочка",
            "сохрани",
            "сохраните",
            "сохраняй",
            "тест",
            "тестирование",
            "тестировать",
            "тут",
            "урвать",
            "урви",
            "участвуй",
            "участие",
            "участвуйте",
            "учите",
            "учим",
            "учиться",
            "учитесь",
            "узнать",
            "узнавать",
            "хотите",
            "хочешь",
            "школа",
            "школе",
            "эксперт",
            "экспертный",
            "экпертов",
            "эксперты",
        ]

        # Подсчитываем количество совпадений
        matches = sum(1 for word in ad_words if word in text)

        # Определяем минимальное количество совпадений в зависимости от длины текста
        text_length = len(text)
        if text_length <= 500:
            min_matches = 4
        elif text_length <= 1000:
            min_matches = 5
        elif text_length <= 1500:
            min_matches = 6
        else:
            min_matches = 7

        is_ad = matches >= min_matches

        if is_ad:
            logger.info(
                f"Обнаружено рекламное сообщение: {matches} совпадений из {min_matches} требуемых"
            )

        return is_ad

    async def save_channel_news(self, channel: Channel, message, chat):
        """Сохраняет новость канала в БД и отправляет уведомление"""
        try:
            # Создаем короткую версию сообщения (до 150 символов)
            full_text = message.text or ""
            news = await ChannelNews.objects.acreate(
                channel=channel,
                message_id=message.id,
                message=full_text,
            )
            logger.debug(
                f"Создана новость ID {news.id} для канала {channel.title}"
            )

            # Отправляем уведомление о новом рекламном посте
            ad_message = NewAdMessage(
                channel_id=channel.telegram_id,
                channel_title=channel.title,
                message_id=message.id,
                message_text=full_text,
                channel_link=f"https://t.me/{channel.main_username}"
                if channel.main_username
                else channel.link_subscription or "",
            )

            # Публикуем уведомление через event_manager
            await event_manager.publish_event(
                EventType.NEW_AD_MESSAGE, ad_message, "bot:new_ad"
            )

            return news

        except Exception as e:
            logger.error(f"Ошибка при сохранении новости: {e}", exc_info=True)
            return None

    async def stop(self):
        """Останавливает daemon"""
        self.running = False
        if self.client:
            await self.client.disconnect()


async def main():
    """Главная функция для запуска daemon"""
    daemon = UserbotDaemon()

    try:
        await daemon.start()
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
        await daemon.stop()
    except Exception as e:
        logger.error(f"Критическая ошибка daemon: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
