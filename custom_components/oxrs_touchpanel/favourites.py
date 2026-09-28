"""Favourite tiles, and the tile setups used most, for filling an empty place quickly.

Favourites are tiles the user keeps, shared by every panel (.storage/
oxrs_touchpanel_favourites): the tile as it was, less its screen and position.

"Frequently used" is worked out from the tiles already set up on every panel: tiles
count as the same setup when they differ only in their place and in what they are
bound to - the entity, and what follows from it (the label naming it, a sub-label or
second sensor, a player's playlists) - since those normally change from panel to panel.
What is left (type, icon, size, colours, background image, album art and the like) is
the setup, and the ones seen most are offered first.

Either is placed through the tile form, filled in from it, so it is checked like any
other new tile.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ACTION_ENTITY,
    CONF_ENTITY_ID,
    CONF_INDICATOR_SECONDARY_ENTITY_ID,
    CONF_LABEL,
    CONF_PLAYLISTS,
    CONF_SCREEN,
    CONF_SUBLABEL_ENTITY_ID,
    CONF_TILE,
    CONF_TYPE,
)
from .tiles import TILE_TYPES

STORAGE_KEY = "oxrs_touchpanel_favourites"
STORAGE_VERSION = 1
_DATA_KEY = "oxrs_touchpanel_favourites_store"
MAX_FAVOURITES = 60
MAX_FREQUENT = 8

# Where a tile sits; never part of what is kept or compared.
PLACE_KEYS = (CONF_SCREEN, CONF_TILE)
# What a tile is bound to, and what follows from that binding.
BINDING_KEYS = (
    CONF_ENTITY_ID,
    CONF_ACTION_ENTITY,
    CONF_SUBLABEL_ENTITY_ID,
    CONF_INDICATOR_SECONDARY_ENTITY_ID,
    CONF_PLAYLISTS,
    CONF_LABEL,
)


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def without_place(tile: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in tile.items() if k not in PLACE_KEYS}


def setup_of(tile: dict[str, Any]) -> dict[str, Any]:
    """A tile's setup: the tile less its place and what it is bound to."""
    return {k: v for k, v in tile.items() if k not in PLACE_KEYS + BINDING_KEYS}


def frequent(tiles: Iterable[dict[str, Any]], limit: int = MAX_FREQUENT) -> list[dict[str, Any]]:
    """The setups used most among these tiles, most first: [{"tile", "count"}].

    Only tiles of a known type count (an old action tile can't be placed from the
    form). Ties keep the order the setups were first seen in.
    """
    counts: dict[str, list[Any]] = {}
    for tile in tiles:
        if not isinstance(tile, dict) or tile.get(CONF_TYPE) not in TILE_TYPES:
            continue
        setup = setup_of(tile)
        entry = counts.setdefault(_key(setup), [setup, 0])
        entry[1] += 1
    ranked = sorted(counts.values(), key=lambda e: -e[1])
    return [{"tile": setup, "count": count} for setup, count in ranked[:limit]]


class Favourites:
    """The favourite tiles, shared by every panel."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.items: list[dict[str, Any]] = []
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        data = await self._store.async_load() or {}
        items = data.get("items") if isinstance(data, dict) else None
        self.items = [
            i for i in items or []
            if isinstance(i, dict) and isinstance(i.get("id"), str) and isinstance(i.get("tile"), dict)
        ]
        self._loaded = True

    async def _async_save(self) -> None:
        await self._store.async_save({"items": self.items})

    def find(self, tile: dict[str, Any]) -> dict[str, Any] | None:
        """The favourite that is this tile (wherever it sits), if any."""
        key = _key(without_place(tile))
        return next((i for i in self.items if _key(i["tile"]) == key), None)

    async def async_add(self, tile: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Keep a tile as a favourite. Returns (the favourite, whether it is new).

        Raises ValueError for a tile that can't be a favourite.
        """
        if tile.get(CONF_TYPE) not in TILE_TYPES:
            raise ValueError("Only tiles of a known type can be favourites; this is an older action tile.")
        existing = self.find(tile)
        if existing is not None:
            return existing, False
        if len(self.items) >= MAX_FAVOURITES:
            raise ValueError(f"There are already {MAX_FAVOURITES} favourites. Remove one first.")
        item = {"id": uuid.uuid4().hex[:12], "tile": without_place(tile), "added": int(time.time())}
        self.items.append(item)
        await self._async_save()
        return item, True

    async def async_remove(self, item_id: str) -> bool:
        before = len(self.items)
        self.items = [i for i in self.items if i["id"] != item_id]
        if len(self.items) == before:
            return False
        await self._async_save()
        return True


async def async_get(hass: HomeAssistant) -> Favourites:
    """The one Favourites store, loaded."""
    favourites = hass.data.get(_DATA_KEY)
    if favourites is None:
        favourites = hass.data[_DATA_KEY] = Favourites(hass)
    await favourites.async_load()
    return favourites
