"""Which board a panel is, and the tile grid it natively has.

The panel's adopt message (stat/<id>/adopt, retained) carries firmware.hardware,
set from the FW_HARDWARE build flag of the OXRS-IO-TouchPanel-ESP32-FW firmware.
Every build environment defines it, but the firmware only emits it
`#if defined(FW_HARDWARE)`, so a custom build may leave it out.

The grid comes from the firmware's own defaults (include/globalDefines.h and
platformio.ini): the 480x480 WT32S3-86 boards set 3x3 explicitly, and the WT32-SC01
family falls back to the firmware default of 2 columns x 3 rows on a 320x480 screen.

A panel's layout is decided ONCE, when it is added, and stored in its config entry
(CONF_LAYOUT). Tile numbers are row-major, so they depend on the column count -
position 4 is row 2 column 1 on three columns but row 2 column 2 on two - which means
changing a grid under existing tiles would silently move every one of them. Entries
created before this existed have no stored layout and keep the 3x3 the integration
always sent, permanently.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from .const import CONF_CLIENT_ID, CONF_HARDWARE, CONF_LAYOUT, DEFAULT_LAYOUT

# The firmware's SCREEN_COLS_MAX / SCREEN_ROWS_MAX.
MAX_GRID = 10

# Choice offered when adding a panel by hand and its board is not in the list.
OTHER_BOARD = "other"


class Board(NamedTuple):
    hardware: str
    cols: int
    rows: int
    width: int
    height: int


BOARDS: dict[str, Board] = {
    b.hardware: b
    for b in (
        Board("WT32S3-86S", 3, 3, 480, 480),
        Board("WT32S3-86V", 3, 3, 480, 480),
        Board("WT32-SC01", 2, 3, 320, 480),
        Board("WT32-SC01-PLUS", 2, 3, 320, 480),
    )
}


# The firmware's defaults (SCREEN_WIDTH / SCREEN_HEIGHT in globalDefines.h), used when a
# panel's board is not known. The smaller screen is the safe assumption: art sized for it
# is never larger than the tile.
DEFAULT_SCREEN = (320, 480)


def screen_size(hardware: str | None) -> tuple[int, int]:
    """(width, height) in pixels of a board's screen, or the firmware default."""
    board = BOARDS.get(hardware or "")
    return (board.width, board.height) if board else DEFAULT_SCREEN


def hardware_from_adopt(payload: Any) -> str | None:
    """The board name in an adopt message, or None if it does not carry one."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    firmware = data.get("firmware") if isinstance(data, dict) else None
    hardware = firmware.get("hardware") if isinstance(firmware, dict) else None
    if isinstance(hardware, str) and hardware.strip():
        return hardware.strip()
    return None


def layout_for(hardware: str | None) -> dict[str, int]:
    """The native grid of a board, or the legacy 3x3 for one we do not know."""
    board = BOARDS.get(hardware or "")
    if board is None:
        return dict(DEFAULT_LAYOUT)
    return {"horizontal": board.cols, "vertical": board.rows}


def layout_from_data(data: Any) -> dict[str, int]:
    """The grid stored in a config entry, or the legacy 3x3 if it has none.

    A stored value that is not a sensible grid is treated as absent rather than
    trusted: the firmware ignores a screen whose layout is out of range.
    """
    stored = data.get(CONF_LAYOUT) if data is not None else None
    if isinstance(stored, dict):
        cols, rows = stored.get("horizontal"), stored.get("vertical")
        if (
            isinstance(cols, int)
            and isinstance(rows, int)
            and not isinstance(cols, bool)
            and not isinstance(rows, bool)
            and 1 <= cols <= MAX_GRID
            and 1 <= rows <= MAX_GRID
        ):
            return {"horizontal": cols, "vertical": rows}
    return dict(DEFAULT_LAYOUT)


def new_entry_data(client_id: str, hardware: str | None) -> dict[str, Any]:
    """Config-entry data for a NEWLY added panel.

    Always stores a layout, even for an unknown board: its presence is what
    marks a panel as new, as opposed to a legacy entry that predates it.
    """
    data: dict[str, Any] = {CONF_CLIENT_ID: client_id, CONF_LAYOUT: layout_for(hardware)}
    if hardware:
        data[CONF_HARDWARE] = hardware
    return data


def board_label(board: Board) -> str:
    """How a board reads in the add-panel dropdown."""
    return f"{board.hardware} ({board.cols} × {board.rows} tiles)"


def describe(hardware: str | None) -> str:
    """One line for the discovery prompt: the board and its grid."""
    layout = layout_for(hardware)
    grid = f"{layout['horizontal']} × {layout['vertical']} tiles"
    return f"{hardware} · {grid}" if hardware else f"board not reported · {grid}"
