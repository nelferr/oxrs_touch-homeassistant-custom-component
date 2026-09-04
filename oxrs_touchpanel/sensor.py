"""Sensors: panel climate telemetry from the tele/ topic."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, signal_tele
from .hub import OxrsPanel


@dataclass(frozen=True, kw_only=True)
class OxrsSensorDescription:
    """Describes a telemetry sensor backed by a hub attribute."""

    key: str
    name: str
    device_class: SensorDeviceClass
    unit: str
    diagnostic: bool = False


SENSORS: tuple[OxrsSensorDescription, ...] = (
    OxrsSensorDescription(
        key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        unit=UnitOfTemperature.CELSIUS,
    ),
    OxrsSensorDescription(
        key="humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        unit=PERCENTAGE,
    ),
    OxrsSensorDescription(
        key="esp32_temp",
        name="CPU temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        unit=UnitOfTemperature.CELSIUS,
        diagnostic=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the telemetry sensors."""
    panel: OxrsPanel = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(OxrsClimateSensor(panel, desc) for desc in SENSORS)


class OxrsClimateSensor(SensorEntity):
    """A single climate reading published by the panel."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, panel: OxrsPanel, description: OxrsSensorDescription) -> None:
        """Initialise the sensor."""
        self._panel = panel
        self._key = description.key
        self._attr_name = description.name
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.unit
        self._attr_unique_id = f"{panel.client_id}_{description.key}"
        self._attr_device_info = panel.device_info
        if description.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        """Return the latest reading for this sensor."""
        return getattr(self._panel, self._key)

    async def async_added_to_hass(self) -> None:
        """Subscribe to telemetry updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_tele(self._panel.client_id),
                self.async_write_ha_state,
            )
        )
