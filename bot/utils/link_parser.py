import re
from typing import List


def parse_channel_links(text: str) -> List[str]:
    """Парсит текст и извлекает все возможные ссылки на каналы для Telethon"""
    potential_links = re.split(r"[\n\r\s,;]+", text.strip())

    valid_links = []
    for link in potential_links:
        link = link.strip()
        if not link:
            continue
        if link not in valid_links:
            valid_links.append(link)

    return valid_links


def is_valid_channel_text(text: str) -> bool:
    """Проверяет содержит ли текст хотя бы одну потенциальную ссылку"""
    links = parse_channel_links(text)
    return len(links) > 0
