"""Binary sensor: panel online/offline (from the MQTT LWT)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, signal_available
from .hub import OxrsPanel


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the online binary sensor."""
    panel: OxrsPanel = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OxrsOnlineSensor(panel)])


class OxrsOnlineSensor(BinarySensorEntity):
    """Reports whether the panel is currently connected."""

    _attr_has_entity_name = True
    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, panel: OxrsPanel) -> None:
        """Initialise the sensor."""
        self._panel = panel
        self._attr_unique_id = f"{panel.client_id}_online"
        self._attr_device_info = panel.device_info

    @property
    def is_on(self) -> bool:
        """Return True when the panel is online."""
        return self._panel.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to availability updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_available(self._panel.client_id),
                self.async_write_ha_state,
            )
        )
