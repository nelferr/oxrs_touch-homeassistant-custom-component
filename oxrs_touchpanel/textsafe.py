"""Text the panel's font can draw.

The firmware draws labels with LVGL's stock Montserrat font, which covers printable
ASCII, the degree sign and the bullet, plus LVGL's symbol block. Any other character
- every accented letter, the en and em dashes, the euro sign - has no glyph there and
shows as nothing or as a box, so a Portuguese or French track title comes out with
holes in it.

A missing character is swapped for a plain equivalent (accent stripped, dash
straightened, ligature spelled out) and only if there is none does it become "?".
Bundling a font in the firmware would render these properly; that is a firmware
change, so until then this keeps the text readable.
"""

from __future__ import annotations

import unicodedata
from typing import Any

# Characters with no accent to strip, or that stripping would not handle.
TRANSLITERATE = {
    "\xdf": "ss", "\xe6": "ae", "\xc6": "AE", "œ": "oe", "Œ": "OE",
    "\xf8": "o", "\xd8": "O", "đ": "d", "Đ": "D", "ł": "l",
    "Ł": "L", "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "€": "EUR", "…": "...",
    "\xa0": " ",
}

# Beyond printable ASCII, the stock font has the degree sign and the bullet, and
# LVGL's symbol glyphs sit in the private-use area.
_EXTRA = {0xB0, 0x2022}
_SYMBOLS = range(0xF000, 0xF900)

# What each key of a tile payload holds, so it is cleaned wherever it appears.
_TEXT_KEYS = ("label", "subLabel", "text")


def _drawable(ch: str) -> bool:
    cp = ord(ch)
    return 0x20 <= cp <= 0x7E or cp in _EXTRA or cp in _SYMBOLS or ch == "\n"


def panel_text(text: Any) -> Any:
    """text with every character the panel font cannot draw replaced. Non-strings pass through."""
    if not isinstance(text, str) or all(_drawable(c) for c in text):
        return text
    out = []
    for ch in text:
        if _drawable(ch):
            out.append(ch)
            continue
        plain = TRANSLITERATE.get(ch)
        if plain is None:
            plain = "".join(
                c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
            )
        if plain == "":
            continue  # a combining mark on its own (decomposed text): nothing to draw
        out.append(plain if all(_drawable(c) for c in plain) else "?")
    return "".join(out)


def clean_payload_text(payload: dict[str, Any]) -> None:
    """Clean, in place, the text a tile or screen payload sends to the panel."""
    for key in _TEXT_KEYS:
        if key in payload:
            payload[key] = panel_text(payload[key])
    names = payload.get("dropDownList")
    if isinstance(names, list):
        payload["dropDownList"] = [panel_text(n) for n in names]
