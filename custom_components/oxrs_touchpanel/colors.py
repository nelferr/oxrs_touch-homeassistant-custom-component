"""Colour helpers shared by the hub (which sends colours) and the options flow
(which stores them), so the two cannot disagree about what a valid colour is.

Colours are stored in the entry options as [r, g, b] and sent to the panel as
the firmware's {"r": .., "g": .., "b": ..}.
"""

from __future__ import annotations

from typing import Any

BLACK = (0, 0, 0)


def normalize_rgb(value: Any) -> tuple[int, int, int] | None:
    """Three channels clamped to 0-255, or None if the value is not a colour.

    The firmware casts each channel to a byte, so an out-of-range number would
    wrap round to a different colour rather than being rejected - a stored 300
    would become 44. Clamping here is what prevents that. Anything that is not
    a list or tuple of exactly three numbers is treated as absent rather than
    guessed at, which is why a bare string like "123" does not count.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        r, g, b = (max(0, min(255, int(c))) for c in value)
    except (TypeError, ValueError):
        return None
    return r, g, b


def rgb_payload(channels: tuple[int, int, int]) -> dict[str, int]:
    """The firmware's {"r", "g", "b"} shape for a colour."""
    r, g, b = channels
    return {"r": r, "g": g, "b": b}


def override_payload(value: Any) -> dict[str, int] | None:
    """Payload for a per-screen or per-tile colour, or None when there is none.

    Pure black is how the firmware spells "unset" at these levels - a screen
    falls back to the global colour and a tile to its screen's - so a stored
    black is the same as no colour at all and is never sent.
    """
    channels = normalize_rgb(value)
    if channels is None or channels == BLACK:
        return None
    return rgb_payload(channels)
