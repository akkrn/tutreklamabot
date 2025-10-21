"""
Сюда вынесен код, занимающийся форматированием сообщений бота для Telegram
"""

import re
from collections import namedtuple
from typing import Iterator

import telegram_text
import telegram_text.bases
from django.template import Context, Template

from bot.constants import MAX_MESSAGE_LENGTH

type MarkdownPatterns = list[tuple[str, str]]
MARKDOWN_FLAVOR_A = [
    (r"\*(.*?)\*", "bold"),  # *bold*
    (r"__(.*?)__", "underlined"),  # __underlined__
    (r"_(.*?)_", "italic"),  # _italic_
    (r"~(.*?)~", "strikethrough"),  # ~~strikethrough~~
    (r"\|\|(.*?)\|\|", "spoiler"),  # ||spoiler||
    (r"\[(.*?)\]\((.*?)\)", "link"),  # [text](url)
]

MARKDOWN_FLAVOR_B = [
    (r"\*\*(.+?)\*\*", "bold"),  # **bold**
    (r"\*(.+?)\*", "italic"),  # *italic*
    (r"__(.+?)__", "italic"),  # __also italic__
    (r"\[(.*?)\]\((.*?)\)", "link"),  # [text](url)
]


def markdown_to_telegram_markdown(text: str) -> str:
    """
    Функция принимает текст с ограниченной markdown-разметкой и возвращает
    этот же текст с сохранением разметки, но отформатированный для диалекта
    "Markdown V2", поддерживаемого Telegram.

    https://core.telegram.org/bots/api#formatting-options

    Ограниченная Markdown-разметка на входе:
    *полужирный*;
    _курсив_;
    __подчёркнутый__;
    ~зачёркнутый~;
    ||спойлер||;
    [название ссылки](URL ссылки)
    """
    parsed = parse_limited_markdown(text)
    return serialize_telegram_markdown_v2(parsed)


def markdown_to_telegram_markdown_chunked(
    text: str, max_chunk_size: int, patterns: None | MarkdownPatterns = None
) -> Iterator[str]:
    """Аналогично markdown_to_telegram_markdown, но текст разбивается на куски длиной не более max_chunk_size"""
    parsed = parse_limited_markdown(text, patterns=patterns)
    return serialize_telegram_markdown_v2_chunked(parsed, max_chunk_size)


MarkdownTextSegment = namedtuple("MarkdownTextSegment", "style text")


def parse_limited_markdown(
    text: str, patterns: None | MarkdownPatterns = None
) -> list[MarkdownTextSegment]:
    """Текст с ограниченной разметкой превращаем в последовательность сегментов со стилями"""
    patterns = patterns or MARKDOWN_FLAVOR_A

    segments = []
    pos = 0

    while pos < len(text):
        matches = [(re.search(pat, text[pos:]), tag) for pat, tag in patterns]
        matches = [
            (m.start(), m.end(), m.groups(), tag) for m, tag in matches if m
        ]

        if not matches:
            segments.append(("text", text[pos:]))
            break

        matches.sort(
            key=lambda m: (m[0], -m[1])
        )  # чтобы '**a**' становилось раньше чем '**a*'
        start, end, groups, tag = matches[0]

        if start > 0:
            segments.append(("text", text[pos : pos + start]))

        if tag == "link":
            segments.append((tag, (groups[0], groups[1])))  # (tag, text, url)
        else:
            segments.append((tag, groups[0]))  # (tag, content)

        pos += end

    return segments


def convert_telegram_markdown_v2(
    segments: list[MarkdownTextSegment],
) -> list[telegram_text.bases.Element]:
    elements: list[telegram_text.bases.Element] = []
    for style, text in segments:
        match style:
            case "bold":
                seg = telegram_text.Bold(text)
            case "italic":
                seg = telegram_text.Italic(text)
            case "underlined":
                seg = telegram_text.Underline(text)
            case "strikethrough":
                seg = telegram_text.Strikethrough(text)
            case "spoiler":
                seg = telegram_text.Spoiler(text)
            case "link":
                seg = telegram_text.Link(text[0], text[1])
            case _:
                seg = telegram_text.PlainText(text)
        elements.append(seg)
    return elements


def serialize_telegram_markdown_v2(segments: list[MarkdownTextSegment]) -> str:
    elements = convert_telegram_markdown_v2(segments)
    return telegram_text.Chain(*elements, sep="").to_markdown()


def serialize_telegram_markdown_v2_chunked(
    segments: list[MarkdownTextSegment], chunk_size: int
) -> Iterator[str]:
    elements = convert_telegram_markdown_v2(segments)
    chunk = ""

    for el in elements:
        el_str = el.to_markdown()

        while len(el_str) > chunk_size:
            if chunk:
                yield chunk
                chunk = ""

            # Пытаемся найти ближайший перенос строки до лимита
            split_index = el_str.rfind("\n", 0, chunk_size)
            if split_index == -1:
                split_index = chunk_size  # если \n  нет — режем как есть

            yield el_str[:split_index]
            el_str = el_str[split_index:]

        new_chunk = chunk + el_str
        if len(new_chunk) > chunk_size:
            yield chunk
            chunk = el_str
        else:
            chunk = new_chunk
    if chunk:
        yield chunk


def render_template(template: str, context: dict) -> str:
    """Выполить рендер текстового шаблона"""
    tpl = Template(template)
    ctx = Context(context)
    rendered_template = tpl.render(ctx)
    return rendered_template


def split_html_message(
    text: str, max_length: int = MAX_MESSAGE_LENGTH
) -> list[str]:
    """Разбивает HTML сообщение на части по \n\n если превышает max_length"""
    if len(text) <= max_length:
        return [text]

    # Разбиваем по \n\n
    parts = text.split("\n\n")
    chunks = []
    current_chunk = ""

    for part in parts:
        # Если добавление части превысит лимит, сохраняем текущий чанк
        if current_chunk and len(current_chunk) + len(part) + 2 > max_length:
            chunks.append(current_chunk.strip())
            current_chunk = part
        else:
            if current_chunk:
                current_chunk += "\n\n" + part
            else:
                current_chunk = part

    # Добавляем последний чанк
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks
