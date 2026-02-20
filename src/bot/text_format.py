from __future__ import annotations

import html
import re

_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_LATEX_SUBSCRIPT_RE = re.compile(r"_\{([^{}\n]+)\}")
_SUBSCRIPT_CHAR_MAP = {
    "0": "\u2080",
    "1": "\u2081",
    "2": "\u2082",
    "3": "\u2083",
    "4": "\u2084",
    "5": "\u2085",
    "6": "\u2086",
    "7": "\u2087",
    "8": "\u2088",
    "9": "\u2089",
    "+": "\u208a",
    "-": "\u208b",
    "=": "\u208c",
    "(": "\u208d",
    ")": "\u208e",
    "a": "\u2090",
    "e": "\u2091",
    "h": "\u2095",
    "i": "\u1d62",
    "j": "\u2c7c",
    "k": "\u2096",
    "l": "\u2097",
    "m": "\u2098",
    "n": "\u2099",
    "o": "\u2092",
    "p": "\u209a",
    "r": "\u1d63",
    "s": "\u209b",
    "t": "\u209c",
    "u": "\u1d64",
    "v": "\u1d65",
    "x": "\u2093",
}


def markdownish_to_telegram_html(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            lines.append(f"<b>{_render_inline(heading.group(1))}</b>")
            continue
        lines.append(_render_inline(line))
    return "\n".join(lines)


def _render_inline(text: str) -> str:
    text = _render_latex_subscripts(text)
    result: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("**", i):
            end = text.find("**", i + 2)
            if end != -1 and end > i + 2:
                result.append(f"<b>{html.escape(text[i + 2:end])}</b>")
                i = end + 2
                continue

        if text[i] == "*":
            end = text.find("*", i + 1)
            if end != -1 and end > i + 1:
                result.append(f"<i>{html.escape(text[i + 1:end])}</i>")
                i = end + 1
                continue

        if text[i] == "`":
            end = text.find("`", i + 1)
            if end != -1 and end > i + 1:
                result.append(f"<code>{html.escape(text[i + 1:end])}</code>")
                i = end + 1
                continue

        result.append(html.escape(text[i]))
        i += 1

    return "".join(result)


def _render_latex_subscripts(text: str) -> str:
    if "_{" not in text:
        return text

    parts: list[str] = []
    i = 0
    while i < len(text):
        tick_start = text.find("`", i)
        if tick_start == -1:
            parts.append(_replace_latex_subscripts(text[i:]))
            break

        parts.append(_replace_latex_subscripts(text[i:tick_start]))
        tick_end = text.find("`", tick_start + 1)
        if tick_end == -1:
            parts.append(text[tick_start:])
            break

        parts.append(text[tick_start : tick_end + 1])
        i = tick_end + 1

    return "".join(parts)


def _replace_latex_subscripts(text: str) -> str:
    return _LATEX_SUBSCRIPT_RE.sub(_subscript_match_replacer, text)


def _subscript_match_replacer(match: re.Match[str]) -> str:
    content = match.group(1)
    converted: list[str] = []
    for char in content:
        value = _SUBSCRIPT_CHAR_MAP.get(char)
        if value is None:
            return match.group(0)
        converted.append(value)
    return "".join(converted)
