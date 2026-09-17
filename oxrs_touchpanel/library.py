"""Shared background-image and custom-icon library for OXRS Touch Panels.

Unlike the earlier per-panel design, this library is NOT tied to any single
config entry. It's backed by Home Assistant's own storage helper
(.storage/oxrs_touchpanel_library), so an image or icon added once is
available to every panel you configure - add it once, use it everywhere.

Each physical panel still needs its own addImage/addIcon commands sent to
it (the panel's RAM is separate hardware), but the SOURCE data - the base64
payload, the name, the format - lives in exactly one place.

The icons shipped in bundled_icons.json are added to the library at startup
(see async_seed_bundled_icons), so they appear alongside user uploads.

OXRS firmware rules (from the docs):
- Both images and icons are referenced by name, not persistent, and must be
  (re)sent after every panel restart.
- Names cannot start with an underscore (reserved for firmware built-ins).
- Custom icons must be PNG. Background images are commonly PNG too, but we
  accept JPG/GIF as well since the firmware's decoder isn't documented as
  PNG-only for backgrounds specifically.
- The encoded (base64) payload should stay under ~4KB to avoid crashing
  the panel.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "oxrs_touchpanel_library"

# OXRS firmware crashes if the encoded (base64) payload exceeds ~4KB.
MAX_ENCODED_SIZE = 4096

# Fixed set of categories for organising custom icons in the picker.
ICON_CATEGORIES: dict[str, str] = {
    "lighting": "Lighting",
    "climate": "Climate",
    "security": "Security",
    "av": "Audio / Video",
    "appliances": "Appliances",
    "energy": "Energy",
    "outdoor": "Outdoor",
    "covers": "Blinds / Covers",
    "misc": "Miscellaneous",
}
DEFAULT_ICON_CATEGORY = "misc"

BUNDLED_ICONS_PATH = Path(__file__).parent / "bundled_icons.json"

# Bundled icons that picture the two states of one thing, as (off, on). A tile
# using either half shows the other half when its state flips, so a door
# contact reads open or closed at a glance.
ICON_STATE_PAIRS: list[tuple[str, str]] = [
    ("door-closed", "door-open"),
    ("window-closed", "window-open"),
    ("garage", "garage-open"),
    ("gate", "gate-open"),
    ("shutter", "shutter-open"),
    ("motion-off", "motion"),
    ("presence-away", "presence-home"),
    ("fan-off", "fan"),
]
_PAIR_BY_ICON: dict[str, tuple[str, str]] = {
    icon: pair for pair in ICON_STATE_PAIRS for icon in pair
}


def _read_bundled_icons() -> list[dict[str, Any]]:
    with BUNDLED_ICONS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["icons"]


class SharedMediaLibrary:
    """Background images and custom icons shared across every OXRS panel.

    One instance lives at hass.data[f"{DOMAIN}_library"], created the first
    time any panel is set up and reused by every panel after that.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.images: dict[str, dict[str, Any]] = {}
        self.icons: dict[str, dict[str, Any]] = {}
        # Names of bundled icons the user deleted, so seeding doesn't bring
        # them back at the next restart.
        self.dismissed_bundled: set[str] = set()
        self._loaded = False
        self._setup_lock = asyncio.Lock()

    async def async_setup(self) -> None:
        """Load from disk and add the bundled icons, exactly once.

        Panels set up concurrently, so a second caller must wait for the first
        to finish - otherwise it would get a library with no icons yet, and
        its panel would boot without them.
        """
        async with self._setup_lock:
            if self._loaded:
                return
            await self.async_load()
            await self.async_seed_bundled_icons()

    async def async_load(self) -> None:
        """Load the library from disk. Safe to call more than once."""
        if self._loaded:
            return
        data = await self._store.async_load() or {}
        self.images = data.get("images", {})
        self.icons = data.get("icons", {})
        self.dismissed_bundled = set(data.get("dismissed_bundled_icons", []))
        self._loaded = True
        _LOGGER.debug(
            f"Shared media library loaded: {len(self.images)} image(s), "
            f"{len(self.icons)} icon(s)"
        )

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "images": self.images,
                "icons": self.icons,
                "dismissed_bundled_icons": sorted(self.dismissed_bundled),
            }
        )

    async def async_save(self) -> None:
        """Public save - for callers (e.g. one-time migrations) that write
        directly into self.images/self.icons rather than via add_image/add_icon."""
        await self._async_save()

    # ── background images ────────────────────────────────────────────────
    async def add_image(
        self, image_id: str, name: str, data: bytes, fmt: str
    ) -> bool:
        """Add or update a background image, available to every panel."""
        try:
            self.images[image_id] = {
                "id": image_id,
                "name": name,
                "data": base64.b64encode(data).decode("utf-8"),
                "format": fmt,
                "size": len(data),
            }
            await self._async_save()
            _LOGGER.info(f"Shared image saved: '{name}' ({fmt}, {len(data)} bytes)")
            return True
        except Exception as err:
            _LOGGER.error(f"Error storing shared image '{name}': {err}", exc_info=True)
            return False

    def get_image(self, image_id: str) -> dict[str, Any] | None:
        return self.images.get(image_id)

    def get_image_by_name(self, name: str) -> dict[str, Any] | None:
        return next((i for i in self.images.values() if i["name"] == name), None)

    def list_images(self) -> list[dict[str, Any]]:
        return [
            {"id": i["id"], "name": i["name"], "format": i["format"], "size": i["size"]}
            for i in self.images.values()
        ]

    async def delete_image(self, image_id: str) -> bool:
        if image_id in self.images:
            name = self.images[image_id]["name"]
            del self.images[image_id]
            await self._async_save()
            _LOGGER.info(f"Shared image deleted: '{name}'")
            return True
        return False

    def build_add_image_payload(self, image_id: str) -> dict[str, Any] | None:
        """OXRS addImage command (Step 1 of the two-step process)."""
        img = self.get_image(image_id)
        if not img:
            return None
        return {"addImage": {"name": img["name"], "imageBase64": img["data"]}}

    # ── custom icons ──────────────────────────────────────────────────────
    async def add_icon(
        self, icon_id: str, name: str, data: bytes, category: str = DEFAULT_ICON_CATEGORY
    ) -> bool:
        """Add or update a custom icon, available to every panel."""
        try:
            # The panel addresses icons by name, so a second entry under the
            # same name could never reach it. The newest upload replaces any
            # older one - including a bundled icon of that name.
            for other_id in [
                i for i, icon in self.icons.items() if icon["name"] == name and i != icon_id
            ]:
                del self.icons[other_id]
            self.icons[icon_id] = {
                "id": icon_id,
                "name": name,
                "data": base64.b64encode(data).decode("utf-8"),
                "category": category if category in ICON_CATEGORIES else DEFAULT_ICON_CATEGORY,
                "size": len(data),
            }
            await self._async_save()
            _LOGGER.info(f"Shared icon saved: '{name}' ({category}, {len(data)} bytes)")
            return True
        except Exception as err:
            _LOGGER.error(f"Error storing shared icon '{name}': {err}", exc_info=True)
            return False

    def get_icon(self, icon_id: str) -> dict[str, Any] | None:
        return self.icons.get(icon_id)

    def get_icon_by_name(self, name: str) -> dict[str, Any] | None:
        return next((i for i in self.icons.values() if i["name"] == name), None)

    def list_icons(self) -> list[dict[str, Any]]:
        return [
            {
                "id": i["id"],
                "name": i["name"],
                "category": i.get("category", DEFAULT_ICON_CATEGORY),
                "bundled": i.get("bundled", False),
            }
            for i in self.icons.values()
        ]

    async def delete_icon(self, icon_id: str) -> bool:
        if icon_id in self.icons:
            icon = self.icons.pop(icon_id)
            if icon.get("bundled"):
                self.dismissed_bundled.add(icon["name"])
            await self._async_save()
            _LOGGER.info(f"Shared icon deleted: '{icon['name']}'")
            return True
        return False

    async def async_seed_bundled_icons(self) -> None:
        """Add the icons shipped with the integration to the library.

        Runs at every start. Bundled icons that are missing get added, and ones
        whose artwork or category changed in an update get refreshed. Two kinds
        of icon are never touched: one the user uploaded under the same name,
        and a bundled one the user deleted - otherwise the deletion would be
        undone at the next restart.

        This costs the panels nothing: only icons their tiles use get sent.
        """
        try:
            bundle = await self.hass.async_add_executor_job(_read_bundled_icons)
        except (OSError, ValueError, KeyError) as err:
            _LOGGER.error("Could not read bundled icons: %s", err)
            return

        by_name = {icon["name"]: icon for icon in self.icons.values()}
        changed = 0
        for entry in bundle:
            name = entry["name"]
            if name in self.dismissed_bundled:
                continue
            existing = by_name.get(name)
            if existing is not None and not existing.get("bundled"):
                continue
            if (
                existing is not None
                and existing["data"] == entry["imageBase64"]
                and existing.get("category") == entry["category"]
            ):
                continue
            self.icons[f"bundled_{name}"] = {
                "id": f"bundled_{name}",
                "name": name,
                "data": entry["imageBase64"],
                "category": entry["category"],
                "size": entry["pngBytes"],
                "bundled": True,
            }
            changed += 1

        if changed:
            await self._async_save()
            _LOGGER.info("Added or refreshed %d bundled icon(s)", changed)

    def state_icon(self, icon: str, is_on: bool) -> str | None:
        """The half of an icon pair a tile should show for its state.

        Returns None when there is nothing to swap: the icon isn't part of a
        pair, or either half is missing from the library (e.g. deleted), in
        which case the tile keeps the icon it was configured with.
        """
        pair = _PAIR_BY_ICON.get(icon)
        if pair is None or any(self.get_icon_by_name(half) is None for half in pair):
            return None
        return pair[1] if is_on else pair[0]

    def paired_icon(self, icon: str) -> str | None:
        """The other half of an icon's pair, if both halves are in the library."""
        pair = _PAIR_BY_ICON.get(icon)
        if pair is None or any(self.get_icon_by_name(half) is None for half in pair):
            return None
        return pair[0] if icon == pair[1] else pair[1]

    def build_add_icon_payload(self, icon_id: str) -> dict[str, Any] | None:
        """OXRS addIcon command."""
        icon = self.get_icon(icon_id)
        if not icon:
            return None
        return {"addIcon": {"name": icon["name"], "imageBase64": icon["data"]}}
