"""Album art for media tiles: fetch it, shrink it to the panel's budget, push it.

The panel accepts one image per addImage message, and the message has a hard
size limit. Measured on real hardware (OXRS TP32, 2026-09-20):

    12,240 B of JSON payload  -> drew
    15,750 B                  -> failed
    16,384 B                  -> failed

so the wall sits somewhere in between. The exact value was not worth the round
trips to pin down; DEFAULT_ALBUM_ART_BUDGET sits just under the largest payload
known to work, and the user can raise it if their firmware build allows more.

Two consequences shape everything here:

* The budget is on the WHOLE JSON payload, not the base64 string and not the
  PNG. An earlier attempt budgeted the base64 and the boundary became
  impossible to read, because the JSON wrapper adds a variable number of bytes.
  encode_for_panel therefore measures the finished payload.

* At 140px that budget buys roughly 24 colours. Photographic covers come out
  posterised but recognisable; logo-style covers survive it cleanly. Larger
  formats are not reachable at all - 300px needs ~48 KB and a full-screen
  460px ~64 KB, both far over the wall - which is why album art is a
  single-tile feature.

JPEG is not supported by the firmware, so everything here produces PNG.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import io
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import ALBUM_ART_FALLBACK_SIZES, ALBUM_ART_SIZE

_LOGGER = logging.getLogger(__name__)

# Never pull a huge original just to crush it to 140px.
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_FETCH_TIMEOUT = 15


def art_image_name(entity_id: str) -> str:
    """Fixed panel-side image name for a player's art.

    One name per player, reused for every track. Re-uploading an existing name
    updates every tile showing it, so a track change needs only the addImage -
    no follow-up tile command. Panel image names may not start with "_", which
    is reserved for the firmware's built-ins.
    """
    return f"art-{entity_id.split('.', 1)[-1]}"[:32]


def art_source_url(hass: HomeAssistant, state: Any) -> str | None:
    """Absolute URL of the current artwork, or None if the player has none.

    Prefers entity_picture_local: it is served by HA's own media_player proxy,
    so it works for any player rather than only for Music Assistant, and its
    token/cache query changes per track, which is what we use to notice a new
    cover. Falls back to entity_picture, which is already absolute.
    """
    if state is None:
        return None
    local = state.attributes.get("entity_picture_local")
    if local:
        try:
            return f"{get_url(hass, prefer_external=False)}{local}"
        except NoURLAvailableError:
            _LOGGER.debug("No internal URL available; falling back to entity_picture")
    remote = state.attributes.get("entity_picture")
    return remote or None


def art_revision(state: Any) -> str | None:
    """Value that changes exactly when the artwork changes.

    Both candidate URLs carry a per-track cache/hash query, so the URL itself
    is the revision marker. Comparing this instead of media_title avoids
    re-encoding on every position update, and still catches two different
    tracks that happen to share a title.
    """
    if state is None:
        return None
    return state.attributes.get("entity_picture_local") or state.attributes.get(
        "entity_picture"
    )


def build_add_image_payload(name: str, png: bytes) -> dict[str, Any]:
    """addImage command for a PNG already encoded to fit."""
    return {"addImage": {"name": name, "imageBase64": base64.b64encode(png).decode()}}


def payload_size(name: str, png: bytes) -> int:
    """Byte length of the finished MQTT payload - the thing that must fit."""
    return len(
        json.dumps(build_add_image_payload(name, png), separators=(",", ":")).encode()
    )


def encode_for_panel(raw: bytes, name: str, budget: int) -> bytes | None:
    """Crop, shrink and quantise until the payload fits. Blocking; run in executor."""
    # Pillow ships with Home Assistant core, so this is not declared in
    # manifest.json - pinning a version here would only risk fighting core's
    # own constraint. Imported lazily and guarded all the same, so a stripped
    # install loses album art rather than the whole integration.
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        _LOGGER.warning("Pillow is not available; album art is disabled")
        return None

    try:
        src = Image.open(io.BytesIO(raw))
        src.load()
    except Exception as err:  # Pillow raises a wide variety here
        _LOGGER.warning(f"Could not decode artwork: {err}")
        return None

    src = src.convert("RGB")
    # Centre-crop to a square so the tile shows the middle of the cover rather
    # than a stretched version of it.
    side = min(src.size)
    left = (src.width - side) // 2
    top = (src.height - side) // 2
    square = src.crop((left, top, left + side, top + side))

    for size in (ALBUM_ART_SIZE, *ALBUM_ART_FALLBACK_SIZES):
        base = square.resize((size, size), Image.LANCZOS)
        # A slight blur removes fine grain that PNG cannot compress, which buys
        # a noticeably larger palette for the same number of bytes.
        base = base.filter(ImageFilter.GaussianBlur(radius=1.0))

        best: bytes | None = None
        best_colors = 0
        lo, hi = 2, 256
        while lo <= hi:
            mid = (lo + hi) // 2
            buf = io.BytesIO()
            base.quantize(colors=mid, method=Image.Quantize.MEDIANCUT).save(
                buf, format="PNG", optimize=True
            )
            png = buf.getvalue()
            if payload_size(name, png) <= budget:
                best, best_colors, lo = png, mid, mid + 1
            else:
                hi = mid - 1

        if best is not None:
            _LOGGER.debug(
                f"Encoded '{name}' at {size}px / {best_colors} colours "
                f"({payload_size(name, best)} B payload, budget {budget})"
            )
            return best

        _LOGGER.debug(f"'{name}' does not fit {budget} B at {size}px; trying smaller")

    _LOGGER.warning(
        f"Could not fit artwork for '{name}' into {budget} bytes at any size"
    )
    return None


async def async_fetch_art(hass: HomeAssistant, url: str) -> bytes | None:
    """Download the artwork, or None if it cannot be fetched."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(url, timeout=_FETCH_TIMEOUT) as resp:
            if resp.status != 200:
                _LOGGER.debug(f"Artwork fetch returned HTTP {resp.status} for {url}")
                return None
            length = resp.content_length
            if length is not None and length > _MAX_SOURCE_BYTES:
                _LOGGER.debug(f"Artwork at {url} too large to fetch ({length} B)")
                return None
            return await resp.content.read(_MAX_SOURCE_BYTES + 1)
    except asyncio.TimeoutError:
        _LOGGER.debug(f"Artwork fetch timed out for {url}")
        return None
    except Exception as err:  # aiohttp raises broadly; art is never critical
        _LOGGER.debug(f"Artwork fetch failed for {url}: {err}")
        return None


async def async_build_art_payload(
    hass: HomeAssistant, url: str, name: str, budget: int
) -> dict[str, Any] | None:
    """Fetch and encode artwork, returning a ready addImage command."""
    raw = await async_fetch_art(hass, url)
    if not raw:
        return None
    png = await hass.async_add_executor_job(
        functools.partial(encode_for_panel, raw, name, budget)
    )
    if png is None:
        return None
    return build_add_image_payload(name, png)
