"""Constants for the OXRS Touch Panel integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "oxrs_touchpanel"

# Separate hass.data namespace from hass.data[DOMAIN] (which maps
# entry_id -> OxrsPanel), so the one shared media library instance can
# never be confused with, or collide with, a panel keyed by its entry_id.
LIBRARY_DATA_KEY = f"{DOMAIN}_library"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]

# Config entry data / options keys
CONF_CLIENT_ID = "client_id"
CONF_NAME = "name"
CONF_TILES = "tiles"

# Optional per-screen display names: {str(screen_number): "Living Room"}.
# Screens without an entry here fall back to the panel's own title, as before.
CONF_SCREEN_NAMES = "screen_names"

# Per-tile keys (old - kept for backward compatibility)
CONF_SCREEN = "screen"
CONF_TILE = "tile"
CONF_TYPE = "type"
CONF_ENTITY_ID = "entity_id"
CONF_LABEL = "label"
CONF_ICON = "icon"

# Common capability: optional secondary entity whose state is shown as the
# tile's subLabel (e.g. "21.4°C", "on 5 mins ago"). Applies to ANY tile type.
CONF_SUBLABEL_ENTITY_ID = "sublabel_entity_id"

# indicator tile: optional second sensor shown alongside the primary one
# (e.g. temperature + humidity in one tile)
CONF_INDICATOR_SECONDARY_ENTITY_ID = "indicator_secondary_entity_id"

# playlists tile: the Music Assistant playlists offered in its dropdown, as
# [{"name": ..., "uri": ...}] in panel order. Capped so the panel's list stays
# short enough to scan without scrolling.
CONF_PLAYLISTS = "playlists"
MAX_PLAYLISTS = 6

# transport tile: show the player's current album art as the tile background.
# The firmware needs non-empty text to hide a tile's icon, so a tile showing
# art cannot also show the _play/_pause icon - see _augment_tile_state.
CONF_ALBUM_ART = "album_art"

# Largest addImage JSON payload, in bytes, that album art may produce.
#
# Measured on a physical OXRS TP32 (2026-09-20): a 12,240 B payload drew, and
# both 15,750 B and 16,384 B failed. The exact wall between them was not worth
# the round trips to find, so the default sits just under the largest payload
# known to work. It is a setting rather than a constant because other firmware
# builds may have a different MQTT buffer - the emulator, for one, swallows
# 64 KB happily.
CONF_ALBUM_ART_BUDGET = "album_art_budget"
DEFAULT_ALBUM_ART_BUDGET = 12000
MIN_ALBUM_ART_BUDGET = 2048
MAX_ALBUM_ART_BUDGET = 65536

# Panel-level display settings, stored in the entry options under this key as
# {firmware_key: int}. Anything missing falls back to the default below.
CONF_PANEL_SETTINGS = "panel_settings"

# The firmware's own config keys, with the defaults and limits it documents
# (main.cpp and the WT32 library's config schema; limits in globalDefines.h),
# as (default, min, max).
# Every one is sent on each conf/ push, defaults included, so the panel ends up
# in a known state rather than in whatever the admin page last left it.
#
# The three timeouts default to 0, which the firmware treats as DISABLED. A
# panel left on those defaults never sleeps, which is what protects the screen
# from burn-in, so anyone who wants that has to set a sleep timeout.
PANEL_SETTINGS: dict[str, tuple[int, int, int]] = {
    "noActivitySecondsToSleep": (0, 0, 3600),  # backlight off
    "noActivitySecondsToHome": (0, 0, 600),  # back to the home screen
    "noActivitySecondsToLock": (0, 0, 3600),  # PIN keypad lock
    "tileBrightnessOn": (100, 75, 100),  # percent
    "tileBrightnessOff": (10, 0, 25),  # percent
    # From the WT32 library rather than the panel firmware. Sets how often the
    # panel reports its temperature and humidity; 0 stops the reports.
    "climateUpdateSeconds": (60, 0, 86400),
}

# Background colour of every screen and tile, stored in the entry options as
# [r, g, b]. The firmware takes it as {"r", "g", "b"}, 0-255 each, applies it to
# all screens and to every tile that has no colour of its own, and treats pure
# black as "unset" - which resolves to its default, also black, so the two are
# the same thing.
CONF_BACKGROUND_COLOR = "background_color"
DEFAULT_BACKGROUND_COLOR = (0, 0, 0)

# The same key holds a single tile's own background colour, on the tile's dict.
# Colours cascade tile -> screen -> panel, and at the tile and screen levels the
# firmware spells "no colour, inherit" as pure black, so black is never stored.

# Per-screen background colours, in the entry options as {"<screen>": [r, g, b]}
# - the same shape as CONF_SCREEN_NAMES.
CONF_SCREEN_COLORS = "screen_colors"

# Colour of an icon in its "on" state, panel-wide, stored as [r, g, b]. Pure
# black is "unset" to the firmware, which then uses its default light green.
CONF_ICON_ON_COLOR = "icon_on_color"
DEFAULT_ICON_ON_COLOR = (91, 190, 91)

# A tile is 140px. If artwork cannot be squeezed into the budget even at two
# colours, the encoder retries smaller rather than giving up outright.
ALBUM_ART_SIZE = 140
ALBUM_ART_FALLBACK_SIZES = (120, 100, 80)

# Per-tile keys (new - flexible actions)
CONF_ACTIONS = "actions"
CONF_ACTION_SEQUENCE = "sequence"
CONF_ACTION_MODE = "mode"
CONF_ACTION_CONDITIONS = "conditions"
CONF_ACTION_ENTITY = "action_entity"  # Optional entity for display/feedback
CONF_ACTION_TILE_TYPE = "action_tile_type"  # User-chosen tile style (cct, slider, updown, etc.)

# Legacy per-panel background image storage key. Only read now by the
# one-time migration in __init__.py that moves old per-entry images into
# the shared library (library.py) - see _async_migrate_legacy_images.
CONF_BACKGROUND_IMAGES = "background_images"

# Panel defaults (WT32S3-86V/86S are 3x3; smaller panels default to 2x3)
DEFAULT_LAYOUT = {"horizontal": 3, "vertical": 3}

# Set once, when a panel is ADDED (see boards.py): the board it reported and the
# tile grid that goes with it. A config entry with no CONF_LAYOUT predates this and
# keeps DEFAULT_LAYOUT for good - changing a grid under existing tiles would move
# every one of them, because tile numbers are row-major.
CONF_HARDWARE = "hardware"
CONF_LAYOUT = "layout"

# CCT firmware ranges (verified from classColorPicker.cpp)
KELVIN_MIN = 2000
KELVIN_MAX = 6000

# Built-in icon names from the firmware icon vault (initIconVault)
BUILTIN_ICONS = [
    "_bulb",
    "_blind",
    "_ceilingfan",
    "_coffee",
    "_door",
    "_locked",
    "_unlocked",
    "_music",
    "_remote",
    "_speaker",
    "_window",
    "_3dprint",
    "_onoff",
    "_play",
    "_pause",
    "_thermometer",
    "_slider",
    "_feed",
    "_thermostat",
]

MANUFACTURER = "OXRS"
MODEL = "TouchPanel ESP32"


def topic_conf(client_id: str) -> str:
    """Config topic for a panel."""
    return f"conf/{client_id}"


def topic_cmnd(client_id: str) -> str:
    """Command topic for a panel."""
    return f"cmnd/{client_id}"


def topic_stat(client_id: str) -> str:
    """State (event) topic for a panel."""
    return f"stat/{client_id}"


def topic_lwt(client_id: str) -> str:
    """Last-will topic for a panel."""
    return f"stat/{client_id}/lwt"


def topic_adopt(client_id: str) -> str:
    """Adopt topic for a panel - RETAINED, and what makes HA offer it for setup."""
    return f"stat/{client_id}/adopt"


def topic_tele(client_id: str) -> str:
    """Telemetry topic for a panel (climate readings)."""
    return f"tele/{client_id}"


def signal_available(client_id: str) -> str:
    """Dispatcher signal fired when a panel's availability changes."""
    return f"{DOMAIN}_{client_id}_available"


def signal_tele(client_id: str) -> str:
    """Dispatcher signal fired when a panel publishes new telemetry."""
    return f"{DOMAIN}_{client_id}_tele"
