"""Firmware updates for a panel, from the OXRS touch panel firmware's GitHub releases.

The firmware (OXRS-IO-TouchPanel-ESP32-FW) is built once per board and connection -
the release assets are named OXRS-IO-TouchPanel-FW_<build>_v<version>_OTA.bin, the
build being a PlatformIO environment such as wt32-86s-wifi_ESP32-S3. A panel says which
board it is (firmware.hardware) and how it is connected (network.mode) in its retained
adopt message, which together name exactly one build.

The update itself is the firmware's own: the OXRS API library (OXRS-IO-API-ESP32-LIB)
takes the image as the body of POST http://<panel>/api/ota, writes it with the ESP32
Update library - which refuses an image that does not validate - and restarts. The
panel's web API has no password, which is the firmware's design; the panel's address
comes from its adopt message (network.ip).

Before anything is sent, the image is checked here too: an ESP32 app image starts with
0xE9 and names the chip it was built for, so an image for the wrong chip is never
offered to the panel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

FIRMWARE_REPO = "OXRS-IO/OXRS-IO-TouchPanel-ESP32-FW"
RELEASES_URL = f"https://github.com/{FIRMWARE_REPO}/releases"
_RELEASES_API = f"https://api.github.com/repos/{FIRMWARE_REPO}/releases?per_page=30"
# GitHub allows 60 unauthenticated requests an hour per address; releases are rare.
RELEASES_MAX_AGE = 6 * 3600
_RELEASES_KEY = "oxrs_touchpanel_firmware_releases"

# ESP-IDF chip ids, in the app image header (esp_image_header_t.chip_id).
CHIP_ESP32 = 0
CHIP_ESP32_S3 = 9
_CHIP_NAMES = {CHIP_ESP32: "ESP32", CHIP_ESP32_S3: "ESP32-S3"}

# (firmware.hardware, network.mode) -> (build, chip), from the firmware's platformio.ini.
BUILDS: dict[tuple[str, str], tuple[str, int]] = {
    ("WT32-SC01", "wifi"): ("wt32-wifi_ESP32", CHIP_ESP32),
    ("WT32-SC01", "ethernet"): ("wt32-eth_ESP32", CHIP_ESP32),
    ("WT32-SC01-PLUS", "wifi"): ("wt32-plus-wifi_ESP32-S3", CHIP_ESP32_S3),
    ("WT32-SC01-PLUS", "ethernet"): ("wt32-plus-eth_ESP32-S3", CHIP_ESP32_S3),
    ("WT32S3-86V", "wifi"): ("wt32-86v-wifi_ESP32-S3", CHIP_ESP32_S3),
    ("WT32S3-86S", "wifi"): ("wt32-86s-wifi_ESP32-S3", CHIP_ESP32_S3),
    ("WT32S3-86S", "ethernet"): ("wt32-86s-eth_ESP32-S3", CHIP_ESP32_S3),
}

# An OTA image is a few hundred kB to a few MB; anything else is not one.
_MIN_IMAGE = 64 * 1024
_MAX_IMAGE = 8 * 1024 * 1024
_DOWNLOAD_TIMEOUT = 180
# The panel reads the whole body before answering, then restarts.
_UPLOAD_TIMEOUT = 240
# How long the panel may take to come back announcing the new version.
_RETURN_TIMEOUT = 300


def asset_name(build: str, version: str) -> str:
    return f"OXRS-IO-TouchPanel-FW_{build}_v{version}_OTA.bin"


def build_for(hardware: str | None, mode: str | None) -> tuple[str, int] | None:
    """The firmware build (and its chip) for a board and connection, if known."""
    if not hardware or not mode:
        return None
    return BUILDS.get((hardware, mode))


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


def check_image(data: bytes, chip: int) -> str | None:
    """Why an image must not be sent to a panel with this chip, or None."""
    if not _MIN_IMAGE <= len(data) <= _MAX_IMAGE:
        return f"the file is {len(data)} bytes, which is not a firmware image"
    if data[0] != 0xE9:
        return "the file is not an ESP32 firmware image"
    image_chip = int.from_bytes(data[12:14], "little")
    if image_chip != chip:
        return (
            f"the file is built for {_CHIP_NAMES.get(image_chip, f'chip {image_chip}')}, "
            f"but this panel is {_CHIP_NAMES.get(chip, f'chip {chip}')}"
        )
    return None


@dataclass
class Release:
    version: str
    prerelease: bool
    published: str
    url: str
    notes: str
    assets: dict[str, str] = field(default_factory=dict)  # name -> download URL


def _parse_releases(raw: Any) -> list[Release]:
    releases = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        version = str(item.get("tag_name") or "").strip().lstrip("v")
        if not version:
            continue
        releases.append(
            Release(
                version=version,
                prerelease=bool(item.get("prerelease")),
                published=str(item.get("published_at") or "")[:10],
                url=str(item.get("html_url") or RELEASES_URL),
                notes=str(item.get("body") or ""),
                assets={
                    str(a.get("name")): str(a.get("browser_download_url"))
                    for a in item.get("assets") or []
                    if isinstance(a, dict) and a.get("name") and a.get("browser_download_url")
                },
            )
        )
    return releases


async def async_releases(hass: HomeAssistant, force: bool = False) -> list[Release]:
    """The firmware's releases, newest first, cached for a few hours.

    Raises aiohttp.ClientError (or asyncio.TimeoutError) when GitHub cannot be reached
    and nothing is cached yet.
    """
    cached = hass.data.get(_RELEASES_KEY)
    if cached and not force and time.monotonic() - cached[0] < RELEASES_MAX_AGE:
        return cached[1]
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            _RELEASES_API,
            headers={"Accept": "application/vnd.github+json"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            releases = _parse_releases(await resp.json())
    except (aiohttp.ClientError, asyncio.TimeoutError):
        if cached:
            return cached[1]  # stale, but better than nothing
        raise
    hass.data[_RELEASES_KEY] = (time.monotonic(), releases)
    return releases


def releases_for(releases: list[Release], build: str) -> list[Release]:
    """The releases that include an OTA image for this build."""
    return [r for r in releases if asset_name(build, r.version) in r.assets]


def latest_stable(releases: list[Release], build: str) -> Release | None:
    """The newest release for this build that is not a pre-release."""
    return next((r for r in releases_for(releases, build) if not r.prerelease), None)


@dataclass
class FirmwareJob:
    """An update in progress (or just finished) on one panel."""

    version: str
    state: str = "downloading"  # downloading, sending, restarting, done, failed
    progress: int | None = 0  # percent, None when it cannot be told
    message: str = ""

    @property
    def active(self) -> bool:
        return self.state not in ("done", "failed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "state": self.state,
            "progress": self.progress,
            "message": self.message,
            "active": self.active,
        }


async def async_install(
    hass: HomeAssistant,
    panel: Any,
    version: str,
    changed: Callable[[], None],
) -> FirmwareJob:
    """Download a release's image for this panel and send it to the panel.

    Progress is kept on panel.firmware_job, and changed() is called whenever it moves
    on. Returns the finished job; its state is "done" or "failed", with the reason.
    """
    job = FirmwareJob(version=version, message="Downloading the firmware")
    panel.firmware_job = job
    changed()

    def fail(message: str) -> FirmwareJob:
        job.state, job.progress, job.message = "failed", None, message
        _LOGGER.warning("Firmware update of %s to %s failed: %s", panel.client_id, version, message)
        changed()
        return job

    build = build_for(panel.reported_hardware, panel.network_mode)
    if build is None:
        return fail("this panel's board and connection are not known, so the right firmware can't be chosen")
    if not panel.ip_address:
        return fail("this panel has not reported its network address")
    name, chip = asset_name(build[0], version), build[1]
    try:
        releases = await async_releases(hass)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return fail(f"couldn't reach GitHub to find the firmware ({err})")
    release = next((r for r in releases if r.version == version), None)
    if release is None or name not in release.assets:
        return fail(f"release {version} has no firmware for this panel ({build[0]})")

    session = async_get_clientsession(hass)
    try:
        async with session.get(
            release.assets[name], timeout=aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT)
        ) as resp:
            resp.raise_for_status()
            total = resp.content_length or 0
            chunks: list[bytes] = []
            received = 0
            async for chunk in resp.content.iter_chunked(64 * 1024):
                chunks.append(chunk)
                received += len(chunk)
                if received > _MAX_IMAGE:
                    return fail("the download is larger than any firmware image")
                if total:
                    percent = min(99, received * 100 // total)
                    if percent >= (job.progress or 0) + 5:
                        job.progress = percent
                        changed()
            image = b"".join(chunks)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return fail(f"couldn't download the firmware ({err})")

    problem = check_image(image, chip)
    if problem:
        return fail(f"not sent to the panel: {problem}")

    job.state, job.progress = "sending", None
    job.message = f"Sending the firmware to the panel ({len(image) // 1024} kB) - don't unplug it"
    changed()
    returned = panel.expect_firmware(version)
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

    job.state, job.message = "restarting", "The panel is restarting with the new firmware"
    changed()
    try:
        await asyncio.wait_for(returned.wait(), _RETURN_TIMEOUT)
    except asyncio.TimeoutError:
        return fail(
            f"the panel took the firmware but has not come back reporting {version}; "
            "check it, and restart it if its screen stays dark"
        )
    job.state, job.progress, job.message = "done", 100, f"Updated to {version}"
    _LOGGER.info("%s updated to firmware %s", panel.client_id, version)
    changed()
    return job
