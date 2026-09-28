"""Firmware update entity: the panel's firmware against the latest OXRS release.

Home Assistant then offers the update where it offers every other one (Settings, the
device page), and installs it through firmware.async_install - the same code the OXRS
panels page uses. Only stable releases are offered here; the page can also install a
pre-release or an older version.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import firmware
from .const import DOMAIN, signal_available, signal_firmware
from .hub import OxrsPanel

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=6)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the firmware update entity."""
    panel: OxrsPanel = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OxrsFirmwareUpdate(panel)], update_before_add=False)


class OxrsFirmwareUpdate(UpdateEntity):
    """The panel's firmware, and the newest stable release built for its board."""

    _attr_has_entity_name = True
    _attr_name = "Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.SPECIFIC_VERSION
        | UpdateEntityFeature.RELEASE_NOTES
    )
    _attr_title = "OXRS touch panel firmware"
    _attr_should_poll = True

    def __init__(self, panel: OxrsPanel) -> None:
        self._panel = panel
        self._attr_unique_id = f"{panel.client_id}_firmware"
        self._attr_device_info = panel.device_info
        self._latest: firmware.Release | None = None

    async def async_added_to_hass(self) -> None:
        for signal in (signal_firmware(self._panel.client_id), signal_available(self._panel.client_id)):
            self.async_on_remove(
                async_dispatcher_connect(self.hass, signal, self._changed)
            )
        # Look the releases up once now, without holding up setup.
        self.hass.async_create_task(self.async_update_ha_state(force_refresh=True))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def _build(self) -> tuple[str, int] | None:
        return firmware.build_for(self._panel.reported_hardware, self._panel.network_mode)

    @property
    def available(self) -> bool:
        return self._panel.available

    @property
    def installed_version(self) -> str | None:
        return self._panel.firmware_version

    @property
    def latest_version(self) -> str | None:
        # Unknown build or no release for it: nothing to offer, so "up to date".
        if self._latest is None:
            return self._panel.firmware_version
        return self._latest.version

    @property
    def release_url(self) -> str | None:
        return self._latest.url if self._latest else firmware.RELEASES_URL

    @property
    def release_summary(self) -> str | None:
        if self._build is None and self._panel.firmware_version:
            return "This panel's board and connection are not known yet, so no update can be offered."
        return None

    @property
    def in_progress(self) -> bool:
        job = self._panel.firmware_job
        return bool(job and job.active)

    @property
    def update_percentage(self) -> int | None:
        job = self._panel.firmware_job
        return job.progress if job and job.active else None

    async def async_update(self) -> None:
        build = self._build
        if build is None:
            self._latest = None
            return
        try:
            releases = await firmware.async_releases(self.hass)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Could not look up OXRS firmware releases: %s", err)
            return
        self._latest = firmware.latest_stable(releases, build[0])

    async def async_release_notes(self) -> str | None:
        return self._latest.notes if self._latest else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        target = version or (self._latest.version if self._latest else None)
        if not target:
            raise HomeAssistantError("There is no firmware release to install for this panel.")
        try:
            job = await self._panel.async_install_firmware(target)
        except RuntimeError as err:
            raise HomeAssistantError(str(err)) from err
        if job.state == "failed":
            raise HomeAssistantError(f"Firmware update failed: {job.message}")
