"""Shared background-image and custom-icon library for OXRS Touch Panels.

Unlike the earlier per-panel design, this library is NOT tied to any single
config entry. It's backed by Home Assistant's own storage helper
(.storage/oxrs_touchpanel_library), so an image or icon added once is
available to every panel you configure - add it once, use it everywhere.

Each physical panel still needs its own addImage/addIcon commands sent to
it (the panel's RAM is separate hardware), but the SOURCE data - the base64
payload, the name, the format - lives in exactly one place.

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

import base64
import logging
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
    "misc": "Miscellaneous",
}
DEFAULT_ICON_CATEGORY = "misc"


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
        self._loaded = False

    async def async_load(self) -> None:
        """Load the library from disk. Safe to call more than once."""
        if self._loaded:
            return
        data = await self._store.async_load() or {}
        self.images = data.get("images", {})
        self.icons = data.get("icons", {})
        self._loaded = True
        _LOGGER.debug(
            f"Shared media library loaded: {len(self.images)} image(s), "
            f"{len(self.icons)} icon(s)"
        )

    async def _async_save(self) -> None:
        await self._store.async_save({"images": self.images, "icons": self.icons})

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
            {"id": i["id"], "name": i["name"], "category": i.get("category", DEFAULT_ICON_CATEGORY)}
            for i in self.icons.values()
        ]

    async def delete_icon(self, icon_id: str) -> bool:
        if icon_id in self.icons:
            name = self.icons[icon_id]["name"]
            del self.icons[icon_id]
            await self._async_save()
            _LOGGER.info(f"Shared icon deleted: '{name}'")
            return True
        return False

    def build_add_icon_payload(self, icon_id: str) -> dict[str, Any] | None:
        """OXRS addIcon command."""
        icon = self.get_icon(icon_id)
        if not icon:
            return None
        return {"addIcon": {"name": icon["name"], "imageBase64": icon["data"]}}
