import re
from typing import List


def parse_channel_links(text: str) -> List[str]:
    """Парсит текст и извлекает только валидные ссылки на Telegram каналы"""
    patterns = [
        r"t\.me/[a-zA-Z0-9_]+",  # t.me/channel
        r"https://t\.me/[a-zA-Z0-9_]+",  # https://t.me/channel
        r"t\.me/\+[a-zA-Z0-9_-]+",  # t.me/+private_link
        r"https://t\.me/\+[a-zA-Z0-9_-]+",  # https://t.me/+private_link
    ]

    valid_links = []

    # Ищем все совпадения по всем паттернам
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # Нормализуем ссылку
            normalized_link = normalize_telegram_link(match)
            if normalized_link and normalized_link not in valid_links:
                valid_links.append(normalized_link)

    return valid_links


def normalize_telegram_link(link: str) -> str:
    """Нормализует Telegram ссылку к единому формату"""
    link = link.strip()

    # Убираем лишние символы в конце
    link = re.sub(r"[.,;!?]+$", "", link)

    # Если это https://t.me, конвертируем в t.me
    if link.startswith("https://t.me/"):
        return link.replace("https://t.me/", "t.me/")

    # Если это t.me, оставляем как есть
    if link.startswith("t.me/"):
        return link

    return link


def is_valid_channel_text(text: str) -> bool:
    """Проверяет содержит ли текст хотя бы одну потенциальную ссылку"""
    links = parse_channel_links(text)
    return len(links) > 0


def extract_forwarded_channel_link(message) -> str | None:
    """Извлекает ссылку на канал из пересланного сообщения"""
    if not message.forward_from_chat:
        return None

    chat = message.forward_from_chat
    if hasattr(chat, "username") and chat.username:
        return f"t.me/{chat.username}"
    if hasattr(chat, "invite_link") and chat.invite_link:
        return chat.invite_link
    return None


def handle_forwarded_message(message) -> List[str]:
    """Обрабатывает пересланное сообщение и возвращает ссылки на каналы"""
    forwarded_link = extract_forwarded_channel_link(message)
    if forwarded_link:
        return [forwarded_link]

    if message.text:
        return parse_channel_links(message.text)

    return []
