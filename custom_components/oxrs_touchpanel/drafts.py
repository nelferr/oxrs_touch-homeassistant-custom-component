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
from typing import Any

from .const import CONF_ENTITY_ID, CONF_SCREEN, CONF_SPAN, CONF_TILE, CONF_TYPE
from .grid import clipped, covered, tile_span
from .tiles import TILE_TYPES

# The options dialog's own limit on a screen number.
MAX_SCREEN = 32


def fingerprint(tiles: list[dict[str, Any]]) -> str:
    """Identifies a tile list, so an apply can tell the stored tiles changed meanwhile."""
    blob = json.dumps(tiles, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _key(tile: Any) -> str:
    return json.dumps(tile, sort_keys=True, ensure_ascii=True, default=str)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _tile_problem(tile: dict[str, Any], cols: int, rows: int) -> str | None:
    """Why a new or changed tile could not have come from the dialog, or None."""
    screen, position = tile.get(CONF_SCREEN), tile.get(CONF_TILE)
    if not _is_int(screen) or not 1 <= screen <= MAX_SCREEN:
        return f"screen must be a number from 1 to {MAX_SCREEN}"
    if not _is_int(position) or not 1 <= position <= cols * rows:
        return f"position must be a number from 1 to {cols * rows}"
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
    touched: list[bool] = []
    problems: list[str] = []
    for tile in draft:
        key = _key(tile)
        if unchanged[key] > 0:
            unchanged[key] -= 1
            touched.append(False)
            continue
        touched.append(True)
        problem = _tile_problem(tile, cols, rows)
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
