"""Checking the tiles the visual editor wants to save.

The editor page stages edits in the browser and sends the whole tile list back with
one "Apply to panel". The page is not trusted to have got it right: every tile it
added or changed must be something the options dialog could have produced, and none
may cover another tile's cells, because the firmware stacks overlapping tiles rather
than refusing them. Tiles it did not touch pass as they are, so an old configuration
(an action tile, a tile off the grid) survives an edit made elsewhere on the panel.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

from .const import CONF_ENTITY_ID, CONF_SCREEN, CONF_SPAN, CONF_TILE, CONF_TYPE, SETTINGS_KEYS
from .grid import clipped, covered, tile_span
from .tiles import TILE_TYPES

# The options dialog's own limit on a screen number.
MAX_SCREEN = 32


def fingerprint(value: Any) -> str:
    """Identifies what an apply will replace, so it can tell it changed meanwhile."""
    blob = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def layout_fingerprint(
    tiles: list[dict[str, Any]], screen_names: Any, screen_colors: Any, settings: Any = None
) -> str:
    """The fingerprint of everything the editor page saves: tiles, screen names and
    colours, and the panel settings."""
    return fingerprint(
        {
            "tiles": tiles,
            "screen_names": screen_names or {},
            "screen_colors": screen_colors or {},
            "settings": settings or {},
        }
    )


def stored_settings(options: Mapping[str, Any]) -> dict[str, Any]:
    """The panel settings part of the options, as stored (SETTINGS_KEYS that are set)."""
    return {key: options[key] for key in SETTINGS_KEYS if key in options}


def _is_screen_key(key: Any) -> bool:
    return isinstance(key, str) and key.isdigit() and 1 <= int(key) <= MAX_SCREEN


def _is_colour(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(_is_int(c) and 0 <= c <= 255 for c in value)
    )


def screens_problems(
    screen_names: Any,
    screen_colors: Any,
    stored_names: dict[str, Any] | None = None,
    stored_colors: dict[str, Any] | None = None,
) -> list[str]:
    """Everything wrong with the screen names and colours sent; empty when they can be saved.

    An entry the same as the stored one passes as it is, as untouched tiles do, so a
    hand-edited value already in the options cannot block every save.
    """
    stored_names = stored_names or {}
    stored_colors = stored_colors or {}
    problems: list[str] = []
    if not isinstance(screen_names, dict):
        problems.append("Screen names were not a mapping.")
    else:
        for key, name in screen_names.items():
            if stored_names.get(key) == name:
                continue
            if not _is_screen_key(key) or not isinstance(name, str):
                problems.append(f"Screen name for {key!r} is not valid.")
    if not isinstance(screen_colors, dict):
        problems.append("Screen colours were not a mapping.")
    else:
        for key, colour in screen_colors.items():
            if stored_colors.get(key) == colour:
                continue
            if not _is_screen_key(key) or not _is_colour(colour):
                problems.append(f"Screen colour for {key!r} is not valid.")
    return problems


def clean_screens(
    screen_names: dict[str, str], screen_colors: dict[str, Any]
) -> tuple[dict[str, str], dict[str, list[int]]]:
    """Screen names and colours as the dialog stores them.

    A blank name is dropped (the screen then shows the panel's title), and so is pure
    black: the firmware reads it as "no colour, follow the panel", so it is never kept.
    """
    names = {
        k: v.strip() if isinstance(v, str) else v
        for k, v in screen_names.items()
        if not isinstance(v, str) or v.strip()
    }
    colours = {
        k: [int(c) for c in v] if _is_colour(v) else v
        for k, v in screen_colors.items()
        if not _is_colour(v) or any(v)
    }
    return names, colours


def _key(tile: Any) -> str:
    return json.dumps(tile, sort_keys=True, ensure_ascii=True, default=str)


def _without_place(tile: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in tile.items() if k not in (CONF_SCREEN, CONF_TILE)}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _tile_problem(
    tile: dict[str, Any], cols: int, rows: int, moved_only: bool = False
) -> str | None:
    """Why a new or changed tile could not have come from the dialog, or None."""
    screen, position = tile.get(CONF_SCREEN), tile.get(CONF_TILE)
    if not _is_int(screen) or not 1 <= screen <= MAX_SCREEN:
        return f"screen must be a number from 1 to {MAX_SCREEN}"
    if not _is_int(position) or not 1 <= position <= cols * rows:
        return f"position must be a number from 1 to {cols * rows}"
    if moved_only:
        # A stored tile that only changed place: what it is was accepted when it was
        # saved (an old action tile included), so only where it now sits is checked.
        size = tile_span(tile.get(CONF_SPAN))
        return "size runs off the grid" if clipped(position, size, cols, rows) != size else None
    if tile.get(CONF_TYPE) not in TILE_TYPES:
        return "unknown tile type"
    entity_id = tile.get(CONF_ENTITY_ID)
    if not isinstance(entity_id, str) or "." not in entity_id:
        return "no entity"
    if CONF_SPAN in tile:
        span = tile[CONF_SPAN]
        size = tile_span(span)
        # tile_span turns anything malformed into 1 x 1, so compare with what was sent.
        if not isinstance(span, (list, tuple)) or list(size) != list(span):
            return "malformed size"
        if clipped(position, size, cols, rows) != size:
            return "size runs off the grid"
        if size == (1, 1):
            return "a 1 x 1 tile carries no size"
    return None


def draft_problems(
    stored: list[dict[str, Any]], draft: Any, cols: int, rows: int
) -> list[str]:
    """Everything wrong with a draft tile list, as sentences; empty when it can be saved."""
    if not isinstance(draft, list) or not all(isinstance(t, dict) for t in draft):
        return ["The tiles sent were not a list of tiles."]

    unchanged = Counter(_key(t) for t in stored)
    # The same tiles with their place left out, to recognise one that was only moved
    # (to another position or screen). Each stored tile accounts for one draft tile.
    placeless = Counter(_key(_without_place(t)) for t in stored)
    touched: list[bool] = []
    for tile in draft:
        key = _key(tile)
        touched.append(unchanged[key] <= 0)
        if not touched[-1]:
            unchanged[key] -= 1
            placeless[_key(_without_place(tile))] -= 1

    problems: list[str] = []
    for tile, is_touched in zip(draft, touched):
        if not is_touched:
            continue
        moved_key = _key(_without_place(tile))
        moved_only = placeless[moved_key] > 0
        if moved_only:
            placeless[moved_key] -= 1
        problem = _tile_problem(tile, cols, rows, moved_only)
        if problem:
            problems.append(
                f"Screen {tile.get(CONF_SCREEN)}, position {tile.get(CONF_TILE)}: {problem}."
            )

    # Overlaps: a cell claimed twice is refused when either tile is new or changed.
    # One that only involves untouched tiles was already in the saved configuration.
    owners: dict[tuple[Any, int], int] = {}
    for index, tile in enumerate(draft):
        screen, position = tile.get(CONF_SCREEN), tile.get(CONF_TILE)
        if not _is_int(position) or not 1 <= position <= cols * rows:
            continue
        for cell in covered(position, tile_span(tile.get(CONF_SPAN)), cols, rows):
            other = owners.setdefault((screen, cell), index)
            if other != index and (touched[index] or touched[other]):
                problems.append(
                    f"Screen {screen}: the tiles at positions {draft[other].get(CONF_TILE)} "
                    f"and {position} overlap."
                )
    # One sentence per pair, in order.
    return list(dict.fromkeys(problems))
