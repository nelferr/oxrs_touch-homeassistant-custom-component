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

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .hub import OxrsPanel


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a panel from a config entry."""
    panel = OxrsPanel(hass, entry)
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
