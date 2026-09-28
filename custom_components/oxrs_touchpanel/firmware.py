"""Firmware updates for a panel, from a firmware file the user chooses.

The file is the firmware's OTA image - OXRS-IO-TouchPanel-ESP32-FW publishes one per
board and connection with each release, named ..._<build>_v<version>_OTA.bin - or a
build of one's own. Nothing is fetched from the internet: the page uploads the file to
Home Assistant, which checks it and sends it to the panel.

The update itself is the firmware's own: the OXRS API library (OXRS-IO-API-ESP32-LIB)
takes the image as the body of POST http://<panel>/api/ota, writes it with the ESP32
Update library - which refuses an image that does not validate - and restarts. The
panel's web API has no password, which is the firmware's design; the panel's address
comes from its adopt message (network.ip).

Before anything is sent, the file is checked here too, so the wrong file never reaches
the panel: an ESP32 app image starts with 0xE9, names the chip it was built for, and
carries an app description (magic 0xABCD5432) at the start of its first segment - which
a bootloader, or a whole-flash "_FLASH.bin" image, does not.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# Where the official firmware files are published, for the page to point to.
RELEASES_URL = "https://github.com/OXRS-IO/OXRS-IO-TouchPanel-ESP32-FW/releases"

# ESP-IDF chip ids, in the app image header (esp_image_header_t.chip_id).
CHIP_ESP32 = 0
CHIP_ESP32_S3 = 9
_CHIP_NAMES = {CHIP_ESP32: "ESP32", CHIP_ESP32_S3: "ESP32-S3"}

# The chip of each board (firmware.hardware), from the firmware's platformio.ini.
CHIPS: dict[str, int] = {
    "WT32-SC01": CHIP_ESP32,
    "WT32-SC01-PLUS": CHIP_ESP32_S3,
    "WT32S3-86V": CHIP_ESP32_S3,
    "WT32S3-86S": CHIP_ESP32_S3,
}

# (firmware.hardware, network.mode) -> the build whose OTA file fits, to tell the user
# which file to choose.
BUILDS: dict[tuple[str, str], str] = {
    ("WT32-SC01", "wifi"): "wt32-wifi_ESP32",
    ("WT32-SC01", "ethernet"): "wt32-eth_ESP32",
    ("WT32-SC01-PLUS", "wifi"): "wt32-plus-wifi_ESP32-S3",
    ("WT32-SC01-PLUS", "ethernet"): "wt32-plus-eth_ESP32-S3",
    ("WT32S3-86V", "wifi"): "wt32-86v-wifi_ESP32-S3",
    ("WT32S3-86S", "wifi"): "wt32-86s-wifi_ESP32-S3",
    ("WT32S3-86S", "ethernet"): "wt32-86s-eth_ESP32-S3",
}

# An OTA image is a few hundred kB to a few MB; anything else is not one.
MIN_IMAGE = 64 * 1024
MAX_IMAGE = 8 * 1024 * 1024
_APP_DESC_OFFSET = 32  # esp_image_header_t (24) + esp_image_segment_header_t (8)
_APP_DESC_MAGIC = 0xABCD5432
# The panel reads the whole body before answering, then restarts.
_UPLOAD_TIMEOUT = 240
# How long the panel may take to come back and announce itself.
_RETURN_TIMEOUT = 300


def build_for(hardware: str | None, mode: str | None) -> str | None:
    """The firmware build whose OTA file fits this board and connection, if known."""
    if not hardware or not mode:
        return None
    return BUILDS.get((hardware, mode))


def chip_for(hardware: str | None) -> int | None:
    return CHIPS.get(hardware or "")


def _adopt(payload: Any) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(section: Any, key: str) -> str | None:
    value = section.get(key) if isinstance(section, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def adopt_facts(payload: Any) -> dict[str, str | None]:
    """What an adopt message says about the panel's firmware and network."""
    data = _adopt(payload)
    return {
        "version": _text(data.get("firmware"), "version"),
        "ip": _text(data.get("network"), "ip"),
        "mode": _text(data.get("network"), "mode"),
        "mac": _text(data.get("network"), "mac"),
    }


def _c_string(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def image_info(data: bytes) -> dict[str, Any]:
    """What an app image says about itself (chip, build date), for the page."""
    info: dict[str, Any] = {"size": len(data)}
    if len(data) >= _APP_DESC_OFFSET + 128 and data[0] == 0xE9:
        chip = int.from_bytes(data[12:14], "little")
        info["chip"] = _CHIP_NAMES.get(chip, f"chip {chip}")
        desc = data[_APP_DESC_OFFSET:]
        if int.from_bytes(desc[0:4], "little") == _APP_DESC_MAGIC:
            built = f"{_c_string(desc[96:112])} {_c_string(desc[80:96])}".strip()
            if built:
                info["built"] = built
    return info


def check_image(data: bytes, chip: int | None, filename: str = "") -> str | None:
    """Why this file must not be sent to a panel with this chip, or None.

    chip None (a board the integration does not know) skips the chip check; the
    panel's own Update library still refuses an image for the wrong chip.
    """
    name = filename.lower()
    if "_flash" in name or name.endswith("flash.bin"):
        return "this is the whole-flash image, for flashing over USB. Choose the file ending in _OTA.bin"
    if not MIN_IMAGE <= len(data) <= MAX_IMAGE:
        return f"the file is {len(data)} bytes, which is not a firmware image"
    if data[0] != 0xE9:
        return "the file is not an ESP32 firmware image"
    if int.from_bytes(data[_APP_DESC_OFFSET:_APP_DESC_OFFSET + 4], "little") != _APP_DESC_MAGIC:
        return "the file is not an app image (a bootloader or whole-flash image?). Choose the file ending in _OTA.bin"
    image_chip = int.from_bytes(data[12:14], "little")
    if chip is not None and image_chip != chip:
        return (
            f"the file is built for {_CHIP_NAMES.get(image_chip, f'chip {image_chip}')}, "
            f"but this panel is {_CHIP_NAMES.get(chip, f'chip {chip}')}"
        )
    return None


@dataclass
class FirmwareJob:
    """An update in progress (or just finished) on one panel."""

    filename: str
    state: str = "sending"  # sending, restarting, done, failed
    message: str = ""
    before: str | None = None  # the version the panel ran before
    after: str | None = None  # the version it announced afterwards

    @property
    def active(self) -> bool:
        return self.state not in ("done", "failed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "state": self.state,
            "message": self.message,
            "before": self.before,
            "after": self.after,
            "active": self.active,
        }


async def async_install(
    hass: HomeAssistant,
    panel: Any,
    image: bytes,
    filename: str,
    changed: Callable[[], None],
) -> FirmwareJob:
    """Send a checked firmware image to the panel and wait for it to come back.

    Progress is kept on panel.firmware_job, and changed() is called whenever it moves
    on. Returns the finished job; its state is "done" or "failed", with the reason.
    """
    job = FirmwareJob(filename=filename, before=panel.firmware_version)
    job.message = f"Sending {filename or 'the firmware'} to the panel ({len(image) // 1024} kB) - don't unplug it"
    panel.firmware_job = job
    changed()

    def fail(message: str) -> FirmwareJob:
        job.state, job.message = "failed", message
        _LOGGER.warning("Firmware update of %s from %s failed: %s", panel.client_id, filename, message)
        changed()
        return job

    if not panel.ip_address:
        return fail("this panel has not reported its network address")
    problem = check_image(image, chip_for(panel.reported_hardware), filename)
    if problem:
        return fail(f"not sent to the panel: {problem}")

    returned = panel.expect_announce()
    session = async_get_clientsession(hass)
    url = f"http://{panel.ip_address}/api/ota"
    try:
        async with session.post(
            url,
            data=image,
            headers={"Content-Type": "application/octet-stream"},
            timeout=aiohttp.ClientTimeout(total=_UPLOAD_TIMEOUT),
        ) as resp:
            if resp.status not in (200, 204):
                detail = (await resp.text()).strip()[:200]
                return fail(f"the panel refused the firmware (HTTP {resp.status}{': ' + detail if detail else ''})")
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return fail(f"couldn't send the firmware to the panel at {panel.ip_address} ({err})")

    job.state, job.message = "restarting", "The panel took the firmware and is restarting with it"
    changed()
    try:
        await asyncio.wait_for(returned.wait(), _RETURN_TIMEOUT)
    except asyncio.TimeoutError:
        return fail("the panel took the firmware but has not come back; check it, and restart it if its screen stays dark")
    job.after = panel.firmware_version
    job.state = "done"
    job.message = (
        f"The panel is back, running {job.after}"
        + (" (the same version it reported before)" if job.after and job.after == job.before else "")
        if job.after
        else "The panel is back"
    )
    _LOGGER.info("%s updated from %s: now running %s", panel.client_id, filename, job.after)
    changed()
    return job
