"""Button: re-push the panel configuration on demand."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .hub import OxrsPanel


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the push-config button."""
    panel: OxrsPanel = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OxrsPushConfigButton(panel)])


class OxrsPushConfigButton(ButtonEntity):
    """Re-sends the screens/tiles config and seeds tile states."""

    _attr_has_entity_name = True
    _attr_name = "Push configuration"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, panel: OxrsPanel) -> None:
        """Initialise the button."""
        self._panel = panel
        self._attr_unique_id = f"{panel.client_id}_push_config"
        self._attr_device_info = panel.device_info

    async def async_press(self) -> None:
        """Push the configuration to the panel."""
        await self._panel.async_push_config()
