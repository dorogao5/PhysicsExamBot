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

_SUPERSCRIPT_CHAR_MAP = {
    "0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3",
    "4": "\u2074", "5": "\u2075", "6": "\u2076", "7": "\u2077",
    "8": "\u2078", "9": "\u2079", "+": "\u207a", "-": "\u207b",
    "n": "\u207f", "i": "\u2071",
}

_GREEK_LETTERS: dict[str, str] = {
    r"\alpha": "\u03b1", r"\beta": "\u03b2", r"\gamma": "\u03b3",
    r"\delta": "\u03b4", r"\epsilon": "\u03b5", r"\varepsilon": "\u03b5",
    r"\zeta": "\u03b6", r"\eta": "\u03b7", r"\theta": "\u03b8",
    r"\vartheta": "\u03d1", r"\iota": "\u03b9", r"\kappa": "\u03ba",
    r"\lambda": "\u03bb", r"\mu": "\u03bc", r"\nu": "\u03bd",
    r"\xi": "\u03be", r"\pi": "\u03c0", r"\rho": "\u03c1",
    r"\sigma": "\u03c3", r"\tau": "\u03c4", r"\upsilon": "\u03c5",
    r"\phi": "\u03c6", r"\varphi": "\u03c6", r"\chi": "\u03c7",
    r"\psi": "\u03c8", r"\omega": "\u03c9",
    r"\Gamma": "\u0393", r"\Delta": "\u0394", r"\Theta": "\u0398",
    r"\Lambda": "\u039b", r"\Xi": "\u039e", r"\Pi": "\u03a0",
    r"\Sigma": "\u03a3", r"\Upsilon": "\u03a5", r"\Phi": "\u03a6",
    r"\Psi": "\u03a8", r"\Omega": "\u03a9",
    r"\ell": "\u2113", r"\infty": "\u221e", r"\partial": "\u2202",
    r"\nabla": "\u2207", r"\cdot": "\u00b7", r"\times": "\u00d7",
    r"\pm": "\u00b1", r"\mp": "\u2213", r"\leq": "\u2264",
    r"\geq": "\u2265", r"\neq": "\u2260", r"\approx": "\u2248",
    r"\equiv": "\u2261", r"\propto": "\u221d", r"\int": "\u222b",
    r"\oint": "\u222e", r"\sum": "\u2211", r"\prod": "\u220f",
    r"\sqrt": "\u221a", r"\perp": "\u22a5", r"\parallel": "\u2225",
    r"\langle": "\u27e8", r"\rangle": "\u27e9",
}

_SORTED_GREEK = sorted(_GREEK_LETTERS.items(), key=lambda x: -len(x[0]))

_BOLD_OPEN = "\x00BO\x00"
_BOLD_CLOSE = "\x00BC\x00"

_VEC_BOLD_RE = re.compile(r"\\vec\s*\{\\(?:mathbf|boldsymbol)\s*\{([^}]+)\}\}")
_VEC_RE = re.compile(r"\\vec\s*\{([^}]+)\}")
_BOLD_CMD_RE = re.compile(r"\\(?:mathbf|boldsymbol|textbf)\s*\{([^}]+)\}")
_FRAC_RE = re.compile(r"\\frac\s*\{([^}]+)\}\s*\{([^}]+)\}")
_SUPER_BRACE_RE = re.compile(r"\^\{([^}]+)\}")
_SUPER_CHAR_RE = re.compile(r"\^([0-9])")
_SUB_CHAR_RE = re.compile(r"_([a-z0-9])")
_LEFTOVER_CMD_RE = re.compile(r"\\[a-zA-Z]+\s*")


def _latex_inline_to_html(latex: str) -> str:
    """Convert an inline LaTeX expression ($...$) to Telegram-safe Unicode+HTML."""
    text = latex.strip()

    text = _VEC_BOLD_RE.sub(
        lambda m: _BOLD_OPEN + m.group(1) + "\u20d7" + _BOLD_CLOSE, text,
    )
    text = _VEC_RE.sub(lambda m: m.group(1) + "\u20d7", text)
    text = _BOLD_CMD_RE.sub(lambda m: _BOLD_OPEN + m.group(1) + _BOLD_CLOSE, text)
    text = _FRAC_RE.sub(r"(\1)/(\2)", text)

    for cmd, char in _SORTED_GREEK:
        text = text.replace(cmd, char)

    text = _LATEX_SUBSCRIPT_RE.sub(_subscript_match_replacer, text)
    text = _SUB_CHAR_RE.sub(
        lambda m: _SUBSCRIPT_CHAR_MAP.get(m.group(1), "_" + m.group(1)), text,
    )
    text = _SUPER_BRACE_RE.sub(_superscript_brace_replacer, text)
    text = _SUPER_CHAR_RE.sub(
        lambda m: _SUPERSCRIPT_CHAR_MAP.get(m.group(1), "^" + m.group(1)), text,
    )

    text = _LEFTOVER_CMD_RE.sub("", text)
    text = text.replace("{", "").replace("}", "")

    text = html.escape(text)
    text = text.replace(_BOLD_OPEN, "<b>").replace(_BOLD_CLOSE, "</b>")
    return text


def _superscript_brace_replacer(match: re.Match[str]) -> str:
    content = match.group(1)
    converted: list[str] = []
    for char in content:
        value = _SUPERSCRIPT_CHAR_MAP.get(char)
        if value is None:
            return "^(" + content + ")"
        converted.append(value)
    return "".join(converted)


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
        if text[i] == "$":
            end = text.find("$", i + 1)
            if end != -1 and end > i + 1:
                result.append(_latex_inline_to_html(text[i + 1 : end]))
                i = end + 1
                continue

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
