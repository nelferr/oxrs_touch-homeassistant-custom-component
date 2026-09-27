"""Tile geometry: which cells a tile covers, and which sizes fit where.

A tile is anchored at its top-left cell. Its number is that cell's row-major position
(1-based), so on a grid of `cols` columns, position p is at row (p-1)//cols and column
(p-1)%cols. A tile of size (w, h) covers w columns and h rows from its anchor.

The firmware (OXRS-IO-TouchPanel-ESP32-FW, classTile::begin) CLIPS a span that runs off
the grid but does NOT reject overlaps: two tiles asked to cover the same cell simply
stack. So this module is the only thing standing between the user and a layout where
one tile hides another, and everything that offers a position or a size goes through it.

Sizes are (width, height) in cells, stored on a tile as [w, h] under CONF_SPAN and
absent for the ordinary 1 x 1.
"""

from __future__ import annotations

from typing import Any

from .const import CONF_SCREEN, CONF_SPAN, CONF_TILE

Size = tuple[int, int]

ONE: Size = (1, 1)

# Styles whose tiles are known to draw sensibly when larger than one cell. The rest
# still work, but nobody has looked at a slider or a thermostat stretched over four
# cells on real hardware, so they are offered marked as experimental.
TESTED_BIG_STYLES = frozenset({"button", "indicator", "buttonPrevNext"})

# From the firmware (classScreen::_makeScreenLayout and classTile::_tileWidth).
FOOTER_HEIGHT = 33
TILE_PADDING = 5

# The sizes people reach for first, in the order they are listed.
_PREFERRED: tuple[Size, ...] = ((2, 1), (1, 2), (2, 2))


def is_size_tested(style: str) -> bool:
    """Whether a tile style is known to look right when made larger."""
    return style in TESTED_BIG_STYLES


def tile_span(value: Any) -> Size:
    """A tile's stored size as (w, h), or 1 x 1 if there is none or it is malformed."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        w, h = value
        if (
            isinstance(w, int)
            and isinstance(h, int)
            and not isinstance(w, bool)
            and not isinstance(h, bool)
            and w >= 1
            and h >= 1
        ):
            return (w, h)
    return ONE


def cell_of(position: int, cols: int) -> tuple[int, int]:
    """(row, column), both 0-based, of a 1-based position."""
    return (position - 1) // cols, (position - 1) % cols


def clipped(position: int, size: Size, cols: int, rows: int) -> Size:
    """The size the firmware will actually draw: cut back to fit inside the grid."""
    row, col = cell_of(position, cols)
    return (max(1, min(size[0], cols - col)), max(1, min(size[1], rows - row)))


def covered(position: int, size: Size, cols: int, rows: int) -> set[int]:
    """Every position a tile covers, after the firmware's clipping."""
    w, h = clipped(position, size, cols, rows)
    row, col = cell_of(position, cols)
    return {(row + dy) * cols + (col + dx) + 1 for dy in range(h) for dx in range(w)}


def occupied(
    tiles: list[dict[str, Any]],
    screen: int,
    cols: int,
    rows: int,
    *,
    skip: int | None = None,
) -> set[int]:
    """Every position on a screen already covered by a tile.

    skip is the index of a tile to leave out - the one being edited, so that it
    does not block its own cells.
    """
    taken: set[int] = set()
    for i, tile in enumerate(tiles):
        if i == skip or tile.get(CONF_SCREEN) != screen:
            continue
        position = tile.get(CONF_TILE)
        if not isinstance(position, int) or isinstance(position, bool):
            continue
        if not 1 <= position <= cols * rows:
            continue
        taken |= covered(position, tile_span(tile.get(CONF_SPAN)), cols, rows)
    return taken


def free_anchors(taken: set[int], cols: int, rows: int) -> list[int]:
    """Positions where at least a 1 x 1 tile fits."""
    return [p for p in range(1, cols * rows + 1) if p not in taken]


def is_full_screen(size: Size, cols: int, rows: int) -> bool:
    """Whether a size covers the whole grid (which needs an otherwise empty screen)."""
    return size == (cols, rows) and cols * rows > 1


def _order(size: Size, cols: int, rows: int) -> tuple:
    if is_full_screen(size, cols, rows):
        return (2, 0, 0)  # full screen always last
    if size in _PREFERRED:
        return (0, _PREFERRED.index(size), 0)
    return (1, size[0] * size[1], size[0])


def fitting_sizes(anchor: int, taken: set[int], cols: int, rows: int) -> list[Size]:
    """Every size that fits at an anchor without leaving the grid or covering a taken cell.

    Includes 1 x 1 when the anchor itself is free. The list is ordered for display:
    the common sizes first, the rest by area, full screen last.
    """
    if not 1 <= anchor <= cols * rows or anchor in taken:
        return []
    row, col = cell_of(anchor, cols)
    sizes: list[Size] = []
    for h in range(1, rows - row + 1):
        for w in range(1, cols - col + 1):
            if not (covered(anchor, (w, h), cols, rows) & taken):
                sizes.append((w, h))
    sizes.sort(key=lambda s: _order(s, cols, rows))
    if ONE in sizes:
        sizes.remove(ONE)
        sizes.insert(0, ONE)
    return sizes


def larger_sizes(anchor: int, taken: set[int], cols: int, rows: int) -> list[Size]:
    """The sizes bigger than 1 x 1 that fit at an anchor."""
    return [s for s in fitting_sizes(anchor, taken, cols, rows) if s != ONE]


def size_value(size: Size) -> str:
    """The form value for a size."""
    return f"{size[0]}x{size[1]}"


def parse_size_value(value: Any) -> Size | None:
    """A size from its form value, or None if it is not one."""
    if not isinstance(value, str) or value.count("x") != 1:
        return None
    left, right = value.split("x")
    if left.isdigit() and right.isdigit() and int(left) >= 1 and int(right) >= 1:
        return (int(left), int(right))
    return None


def size_label(size: Size, cols: int, rows: int, *, experimental: bool = False) -> str:
    """How a size reads in the dropdown."""
    if is_full_screen(size, cols, rows):
        text = f"Full screen ({size[0]} × {size[1]})"
    else:
        text = f"{size[0]} × {size[1]}"
    return f"{text} (experimental)" if experimental else text


def tile_pixels(screen_w: int, screen_h: int, cols: int, rows: int, size: Size) -> Size:
    """Pixel size of a tile, as the firmware lays it out.

    A cell is the screen width divided by the columns wide, and the screen height
    less a 33 px footer divided by the rows tall (integer division both times); a
    tile of size (w, h) is w cells by h cells less 5 px of padding on every side.
    So a tile is NOT square: on either 480 x 480 or 320 x 480 hardware with a
    3 x 3 or 2 x 3 grid a 1 x 1 is 150 x 139 px, and a 3 x 3 on the 480 board is
    470 x 437.
    """
    cell_w = screen_w // cols
    cell_h = (screen_h - FOOTER_HEIGHT) // rows
    return cell_w * size[0] - 2 * TILE_PADDING, cell_h * size[1] - 2 * TILE_PADDING


def span_payload(position: int, value: Any, cols: int, rows: int) -> dict[str, int] | None:
    """The firmware's span object for a stored size, or None for an ordinary tile.

    Clipped to the grid exactly as the firmware would, so what is sent is what will
    be drawn.
    """
    w, h = clipped(position, tile_span(value), cols, rows)
    if (w, h) == ONE:
        return None
    return {"right": w, "down": h}
