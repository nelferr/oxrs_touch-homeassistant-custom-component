"""Sensors: panel climate readings and health diagnostics from the tele/ topic."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, signal_tele
from .hub import OxrsPanel


@dataclass(frozen=True, kw_only=True)
class OxrsSensorDescription:
    """Describes a telemetry sensor backed by a hub attribute."""

    key: str
    name: str
    device_class: SensorDeviceClass | None = None
    unit: str | None = None
    state_class: SensorStateClass = SensorStateClass.MEASUREMENT
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
    # Health of the panel itself. Uptime falling between two readings is how an
    # unexpected restart is told apart from a dropped connection.
    OxrsSensorDescription(
        key="uptime",
        name="Uptime",
        device_class=SensorDeviceClass.DURATION,
        unit=UnitOfTime.SECONDS,
        diagnostic=True,
    ),
    OxrsSensorDescription(
        key="wifi_rssi",
        name="Wi-Fi signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        unit=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        diagnostic=True,
    ),
    OxrsSensorDescription(
        key="wifi_disconnects",
        name="Wi-Fi disconnects",
        state_class=SensorStateClass.TOTAL_INCREASING,
        diagnostic=True,
    ),
    OxrsSensorDescription(
        key="heap_free",
        name="Free memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        unit=UnitOfInformation.BYTES,
        diagnostic=True,
    ),
    OxrsSensorDescription(
        key="heap_max_alloc",
        name="Largest free memory block",
        device_class=SensorDeviceClass.DATA_SIZE,
        unit=UnitOfInformation.BYTES,
        diagnostic=True,
    ),
    OxrsSensorDescription(
        key="psram_free",
        name="Free PSRAM",
        device_class=SensorDeviceClass.DATA_SIZE,
        unit=UnitOfInformation.BYTES,
        diagnostic=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the telemetry and diagnostic sensors."""
    panel: OxrsPanel = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            *(OxrsClimateSensor(panel, desc) for desc in SENSORS),
            OxrsFirmwareSensor(panel),
            OxrsRestartSensor(panel),
        ]
    )


class OxrsClimateSensor(SensorEntity):
    """A single reading published by the panel."""

    _attr_has_entity_name = True

    def __init__(self, panel: OxrsPanel, description: OxrsSensorDescription) -> None:
        """Initialise the sensor."""
        self._panel = panel
        self._key = description.key
        self._attr_name = description.name
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
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


class OxrsFirmwareSensor(SensorEntity):
    """The firmware version the panel reports.

    Home Assistant keeps the history of this sensor, so a run of restarts can be
    lined up with the firmware that was installed at the time.
    """

    _attr_has_entity_name = True
    _attr_name = "Firmware version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, panel: OxrsPanel) -> None:
        """Initialise the sensor."""
        self._panel = panel
        self._attr_unique_id = f"{panel.client_id}_firmware_version"
        self._attr_device_info = panel.device_info

    @property
    def native_value(self) -> str | None:
        """Return the reported firmware version."""
        return self._panel.firmware_version

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates (a version arrives with the adopt message)."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_tele(self._panel.client_id),
                self.async_write_ha_state,
            )
        )


class OxrsRestartSensor(RestoreEntity, SensorEntity):
    """How many times the panel has restarted without being asked to.

    Counted when its uptime is seen to go backwards, so a restart that happens
    between two reports is still counted, once. The Reboot button is not counted.
    The attributes say when the last one was, how long the panel had been running
    and which firmware it had. The count is restored across a Home Assistant
    restart, so it is only as complete as the time Home Assistant was running.
    """

    _attr_has_entity_name = True
    _attr_name = "Unexpected restarts"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, panel: OxrsPanel) -> None:
        """Initialise the sensor."""
        self._panel = panel
        self._attr_unique_id = f"{panel.client_id}_unexpected_restarts"
        self._attr_device_info = panel.device_info

    @property
    def native_value(self) -> int:
        """Return the number of unexpected restarts seen."""
        return self._panel.reboots

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return details of the most recent unexpected restart."""
        return {
            "last_restart": self._panel.last_reboot,
            "uptime_before_restart_seconds": self._panel.last_reboot_uptime,
            "firmware_at_restart": self._panel.last_reboot_firmware,
        }

    async def async_added_to_hass(self) -> None:
        """Restore the count, then follow the panel."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            try:
                self._panel.reboots = max(self._panel.reboots, int(float(last.state)))
            except (TypeError, ValueError):
                pass
            attrs = last.attributes
            if self._panel.last_reboot is None:
                self._panel.last_reboot = attrs.get("last_restart")
                self._panel.last_reboot_uptime = attrs.get("uptime_before_restart_seconds")
                self._panel.last_reboot_firmware = attrs.get("firmware_at_restart")
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_tele(self._panel.client_id),
                self.async_write_ha_state,
            )
        )
