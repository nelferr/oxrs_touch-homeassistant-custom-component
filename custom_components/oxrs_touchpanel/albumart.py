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

Big tiles. A tile larger than one cell is bigger than the image the panel will accept,
so the image is enlarged ON the panel: the firmware's backgroundImage takes a zoom of
50-200 %. The source image is therefore made small - the tile's pixel size divided by
the zoom, capped at a configurable edge length - and the panel does the enlarging, so
the byte budget and the decoded size stay what they are for a 1 x 1 tile. Tiles are not
square (a 1 x 1 is 150 x 139 px, see grid.tile_pixels), so the cover is cropped to the
tile's own proportions rather than to a square. Where the capped image is smaller than
the tile, the tile's background colour is set to the cover's edge colour so the margin
reads as part of the picture, and the title and artist are drawn INTO the image because
the firmware's own text is a fixed, small size.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import io
import json
import logging
import unicodedata
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import ALBUM_ART_FALLBACK_SIZES, ALBUM_ART_SIZE
from .grid import ONE, Size

_LOGGER = logging.getLogger(__name__)

# Never pull a huge original just to crush it to 140px.
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_READ_CHUNK = 64 * 1024
_FETCH_TIMEOUT = 15

# When an image will not fit its budget, try again smaller. The same ratios the
# fixed-size fallbacks always were (120, 100 and 80 px of 140), so a 1 x 1 tile
# behaves exactly as before, and a big tile shrinks in proportion.
_FALLBACK_SCALES = tuple(s / ALBUM_ART_SIZE for s in ALBUM_ART_FALLBACK_SIZES)

# Title and artist are only drawn on tiles at least this many cells on each side.
# On anything smaller the lines would be a few pixels tall.
TEXT_MIN_CELLS = 2


def art_image_name(entity_id: str, size: Size = ONE) -> str:
    """Fixed panel-side image name for a player's art on a tile of a given size.

    One name per player and size, reused for every track. Re-uploading an existing
    name updates every tile showing it, so a track change needs only the addImage -
    no follow-up tile command. Panel image names may not start with "_", which is
    reserved for the firmware's built-ins.

    The size is part of the name because the same player can be shown on tiles of
    different sizes, and each needs its own image: a 1 x 1 tile's name is unchanged
    (art-hifi) and a 2 x 2 tile's is art-hifi-2x2. The 32-character limit is kept
    by trimming the player's name, never the size.
    """
    suffix = "" if size == ONE else f"-{size[0]}x{size[1]}"
    return f"art-{entity_id.split('.', 1)[-1]}"[: 32 - len(suffix)] + suffix


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


@dataclass(frozen=True)
class EncodedArt:
    """A cover encoded for the panel, and what was learned making it."""

    png: bytes
    size: tuple[int, int]  # pixels of the image actually produced
    color: tuple[int, int, int]  # the cover's edge colour
    text_drawn: bool  # whether title/artist were drawn into the image


@dataclass(frozen=True)
class ArtTarget:
    """What a transport tile needs to know to show its player's art.

    zoom is None on a 1 x 1 tile, where the image is shown at its own size; on a
    larger tile it is the firmware zoom percentage. color is the cover's edge
    colour to paint behind an image that is smaller than the tile.
    """

    name: str
    size: Size
    zoom: int | None
    color: tuple[int, int, int] | None
    loaded: bool
    text_in_image: bool


def big_art_geometry(tile_px: Size, zoom: int, max_source: int) -> Size:
    """Pixel size of the image to upload for a tile of tile_px pixels.

    Shown at `zoom` percent it should fill the tile, so it is the tile size divided
    by the zoom - but never larger than max_source on its longest edge, which is
    what keeps the decoded image, and so the panel's memory use, bounded however
    big the tile is. Proportions follow the tile, so a wide tile gets a wide image.
    The zoom is only ever an enlargement (100-200): the firmware allows 50-200, but
    shrinking a small image gains nothing.
    """
    zoom = max(100, min(200, int(zoom)))
    w, h = tile_px[0] * 100 / zoom, tile_px[1] * 100 / zoom
    scale = min(1.0, max_source / max(w, h))
    return max(16, round(w * scale)), max(16, round(h * scale))


def _cover_crop(img: Any, w: int, h: int) -> Any:
    """Crop the middle of an image to the proportions w:h."""
    sw, sh = img.size
    # max(1, ...): rounding a very thin source (a 1 px wide cover) could otherwise
    # ask for a crop of no pixels at all.
    if sw / sh > w / h:
        new_w = max(1, round(sh * w / h))
        left = (sw - new_w) // 2
        return img.crop((left, 0, left + new_w, sh))
    new_h = max(1, round(sw * h / w))
    top = (sh - new_h) // 2
    return img.crop((0, top, sw, top + new_h))


def _edge_color(image_module: Any, img: Any) -> tuple[int, int, int]:
    """The average colour of the outer ring of an image."""
    w, h = img.size
    t = max(2, min(w, h) // 16)
    strips = (
        img.crop((0, 0, w, t)),
        img.crop((0, h - t, w, h)),
        img.crop((0, 0, t, h)),
        img.crop((w - t, 0, w, h)),
    )
    means = [s.resize((1, 1), image_module.BOX).getpixel((0, 0)) for s in strips]
    return tuple(round(sum(m[i] for m in means) / 4) for i in range(3))  # type: ignore[return-value]


def _load_font(font_module: Any, px: int) -> Any | None:
    """A scalable font at px pixels, or None if this Pillow cannot provide one."""
    try:
        font = font_module.load_default(size=px)  # Pillow 10.1+, needs FreeType
    except (TypeError, OSError, ValueError, AttributeError):
        return None
    return font if isinstance(font, font_module.FreeTypeFont) else None


# A code point no font defines, so drawing it shows that font's "missing glyph" box.
NOTDEF_PROBE = chr(0xFFFF)

# Pillow's built-in font is close to ASCII-only. Measured: every accented letter, the
# en and em dashes and the euro sign come out as an empty box (the curly quotes and the
# ellipsis are fine). These are the common characters that stripping an accent cannot
# handle by itself. The quote entries never fire with this font but cost nothing, and
# cover a font that lacks them.
_TRANSLITERATE = {
    '\xdf': 'ss', '\xe6': 'ae', '\xc6': 'AE', '\u0153': 'oe', '\u0152': 'OE', '\xf8': 'o',
    '\xd8': 'O', '\u0111': 'd', '\u0110': 'D', '\u0142': 'l', '\u0141': 'L', '\u2018': "'",
    '\u2019': "'", '\u201c': '"', '\u201d': '"', '\u2013': '-', '\u2014': '-',
    '\u20ac': 'EUR',
}


def _glyph_safe(font: Any, text: str) -> str:
    """text with every character the font cannot draw replaced by something it can.

    A character the font lacks is not skipped - it is drawn as an empty box, which
    for Portuguese, French or Spanish titles would show on nearly every track. So each
    such character is first swapped for a plain equivalent (accents stripped, dashes
    and quotes straightened, a few ligatures spelled out) and only if that fails
    becomes "?". Bundling a font would render these properly; this needs no extra file.
    """
    notdef = font.getmask(NOTDEF_PROBE)
    missing = (notdef.size, bytes(notdef))

    def has(char: str) -> bool:
        mask = font.getmask(char)
        return (mask.size, bytes(mask)) != missing

    out = []
    for ch in text:
        if has(ch):
            out.append(ch)
            continue
        plain = _TRANSLITERATE.get(ch)
        if plain is None:
            plain = "".join(
                c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
            )
        if plain == "":
            continue  # a combining mark on its own (decomposed text): nothing to draw
        out.append(plain if all(has(c) for c in plain) else "?")
    return "".join(out)


def _one_line(text: str) -> str:
    """text as a single printable line.

    Track metadata is not always tidy: a stray newline or tab makes Pillow refuse to
    measure the string at all ("can't measure length of multiline text"), which
    would stop the whole cover updating for that track. Whitespace of any kind
    becomes a single space, and anything unprintable is dropped.
    """
    return "".join(ch for ch in " ".join(text.split()) if ch.isprintable())


def _fit_text(draw: Any, text: str, font: Any, max_width: float) -> str:
    """text, cut with an ellipsis if it is wider than max_width."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    for end in range(len(text) - 1, 0, -1):
        candidate = text[:end].rstrip() + "..."
        if draw.textlength(candidate, font=font) <= max_width:
            return candidate
    return text[:1]


def _overlay_text(
    modules: tuple[Any, Any, Any], img: Any, title: str | None, artist: str | None
) -> tuple[Any, bool]:
    """Draw title and artist on a dark band along the bottom of the image.

    A solid band rather than text straight onto the cover: it stays legible on any
    picture, and a flat area costs a PNG almost nothing next to the photograph.
    Returns the image and whether anything was drawn (False if this Pillow has no
    scalable font, or there was nothing to say).
    """
    image_module, draw_module, font_module = modules
    lines = [c for c in (_one_line(s) for s in (title, artist) if s) if c]
    if not lines:
        return img, False
    w, h = img.size
    sizes = [max(10, round(h * 0.11)), max(8, round(h * 0.08))]
    fonts = [_load_font(font_module, px) for px in sizes[: len(lines)]]
    if any(f is None for f in fonts):
        return img, False

    lines = [_glyph_safe(f, line) for line, f in zip(lines, fonts)]
    pad = max(3, round(h * 0.03))
    draw = draw_module.Draw(img)
    heights = []
    for f in fonts:
        _, top, _, bottom = f.getbbox("Ag")
        heights.append(bottom - top + 2)
    band = pad * 2 + sum(heights)
    draw.rectangle((0, h - band, w, h), fill=(16, 16, 16))
    y = h - band + pad
    for text, f, lh, colour in zip(lines, fonts, heights, ((255, 255, 255), (200, 200, 200))):
        draw.text((pad, y), _fit_text(draw, text, f, w - pad * 2), font=f, fill=colour)
        y += lh
    return img, True


def encode_art(
    raw: bytes,
    name: str,
    budget: int,
    size: Size = (ALBUM_ART_SIZE, ALBUM_ART_SIZE),
    *,
    title: str | None = None,
    artist: str | None = None,
    text: bool = False,
) -> EncodedArt | None:
    """Crop, shrink and quantise a cover until the payload fits. Blocking; run in executor.

    size is the image to aim for in pixels; the cover is cropped to its proportions.
    With text, the title and artist are drawn into the image. If nothing fits at
    that size it is tried smaller, and the result reports the size actually used.
    """
    # Pillow ships with Home Assistant core, so this is not declared in
    # manifest.json - pinning a version here would only risk fighting core's
    # own constraint. Imported lazily and guarded all the same, so a stripped
    # install loses album art rather than the whole integration.
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
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
    # Crop the middle of the cover to the tile's proportions, so the tile shows
    # the middle of the picture rather than a stretched version of it.
    cropped = _cover_crop(src, *size)

    for scale in (1.0, *_FALLBACK_SCALES):
        w, h = max(8, round(size[0] * scale)), max(8, round(size[1] * scale))
        base = cropped.resize((w, h), Image.LANCZOS)
        # A slight blur removes fine grain that PNG cannot compress, which buys
        # a noticeably larger palette for the same number of bytes.
        base = base.filter(ImageFilter.GaussianBlur(radius=1.0))
        color = _edge_color(Image, base)
        drawn = False
        if text:
            base, drawn = _overlay_text((Image, ImageDraw, ImageFont), base, title, artist)

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
                f"Encoded '{name}' at {w}x{h}px / {best_colors} colours "
                f"({payload_size(name, best)} B payload, budget {budget})"
            )
            return EncodedArt(best, (w, h), color, drawn)

        _LOGGER.debug(f"'{name}' does not fit {budget} B at {w}x{h}px; trying smaller")

    _LOGGER.warning(
        f"Could not fit artwork for '{name}' into {budget} bytes at any size"
    )
    return None


def encode_for_panel(raw: bytes, name: str, budget: int) -> bytes | None:
    """The PNG for an ordinary 1 x 1 tile, or None. Kept for callers that only want bytes."""
    result = encode_art(raw, name, budget)
    return result.png if result else None


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
            # Read to the end of the stream in chunks. Do NOT use
            # resp.content.read(n): for n > 0 aiohttp returns whatever is
            # buffered so far, up to n bytes, not the whole body, so any cover
            # that arrives in more than one chunk came back truncated and
            # Pillow refused it. The cap is enforced here as the data arrives,
            # because a response with no Content-Length gives nothing to trust.
            body = bytearray()
            async for chunk in resp.content.iter_chunked(_READ_CHUNK):
                body += chunk
                if len(body) > _MAX_SOURCE_BYTES:
                    _LOGGER.debug(f"Artwork at {url} exceeded {_MAX_SOURCE_BYTES} B")
                    return None
            return bytes(body)
    except asyncio.TimeoutError:
        _LOGGER.debug(f"Artwork fetch timed out for {url}")
        return None
    except Exception as err:  # aiohttp raises broadly; art is never critical
        _LOGGER.debug(f"Artwork fetch failed for {url}: {err}")
        return None


async def async_build_art(
    hass: HomeAssistant,
    url: str,
    name: str,
    budget: int,
    size: Size = (ALBUM_ART_SIZE, ALBUM_ART_SIZE),
    *,
    title: str | None = None,
    artist: str | None = None,
    text: bool = False,
) -> tuple[dict[str, Any], EncodedArt] | None:
    """Fetch and encode artwork: the addImage command and what was learned making it."""
    raw = await async_fetch_art(hass, url)
    if not raw:
        return None
    encoded = await hass.async_add_executor_job(
        functools.partial(
            encode_art, raw, name, budget, size, title=title, artist=artist, text=text
        )
    )
    if encoded is None:
        return None
    return build_add_image_payload(name, encoded.png), encoded


async def async_build_art_payload(
    hass: HomeAssistant, url: str, name: str, budget: int
) -> dict[str, Any] | None:
    """Fetch and encode artwork for a 1 x 1 tile, returning a ready addImage command."""
    built = await async_build_art(hass, url, name, budget)
    return built[0] if built else None
