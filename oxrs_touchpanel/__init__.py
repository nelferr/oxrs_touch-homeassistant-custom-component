"""The OXRS Touch Panel integration.

Phase 1 (Current): Flexible action sequences support
- Added OxrsTileAction model supporting Home Assistant Script system
- Supports service sequences, templates, and conditions
- Backward compatible with existing tile-based configuration
- See models.py for implementation details

Future phases:
- Phase 2: Config UI for action builder
- Phase 3: Remove hardcoded tile types
- Phase 4: Advanced features (conditions, retries, error handling)
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_BACKGROUND_IMAGES, DOMAIN, LIBRARY_DATA_KEY, PLATFORMS
from .hub import OxrsPanel
from .library import SharedMediaLibrary

_LOGGER = logging.getLogger(__name__)


async def _async_get_library(hass: HomeAssistant) -> SharedMediaLibrary:
    """Return the one shared library instance, creating/loading it once."""
    library = hass.data.get(LIBRARY_DATA_KEY)
    if library is None:
        library = SharedMediaLibrary(hass)
        hass.data[LIBRARY_DATA_KEY] = library
    await library.async_setup()  # no-op once ready; concurrent callers wait
    return library


async def _async_migrate_legacy_images(
    hass: HomeAssistant, entry: ConfigEntry, library: SharedMediaLibrary
) -> None:
    """One-time move of a panel's own background images into the shared library.

    Earlier versions stored background images inside each panel's own config
    entry options (CONF_BACKGROUND_IMAGES), private to that one panel. This
    moves any such images into the shared library - available to every panel
    from then on - and clears them from the entry so they aren't migrated
    again or drift out of sync with the shared copy.

    Must run before the update listener is registered (see async_setup_entry)
    so that updating the entry's options here doesn't trigger a self-reload.
    """
    legacy = entry.options.get(CONF_BACKGROUND_IMAGES)
    if not legacy:
        return

    migrated = 0
    for image_id, img in legacy.items():
        if image_id in library.images:
            continue  # already migrated (e.g. a previous reload)
        name = img.get("image_name") or img.get("name")
        data = img.get("image_data") or img.get("data")
        fmt = img.get("image_format") or img.get("format") or "png"
        size = img.get("image_size") or img.get("size") or 0
        if not name or not data:
            continue
        library.images[image_id] = {
            "id": image_id,
            "name": name,
            "data": data,
            "format": fmt,
            "size": size,
        }
        migrated += 1

    if migrated:
        await library.async_save()
        _LOGGER.info(
            f"Migrated {migrated} background image(s) from '{entry.title}' "
            f"into the shared library"
        )

    # Clear the legacy per-entry copy either way (empty dict is also worth
    # tidying up), now that it lives in the shared library.
    new_options = dict(entry.options)
    new_options.pop(CONF_BACKGROUND_IMAGES, None)
    hass.config_entries.async_update_entry(entry, options=new_options)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a panel from a config entry."""
    library = await _async_get_library(hass)
    await _async_migrate_legacy_images(hass, entry, library)

    panel = OxrsPanel(hass, entry, library)
    await panel.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = panel

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        panel: OxrsPanel = hass.data[DOMAIN].pop(entry.entry_id)
        await panel.async_unload()
    return unloaded
