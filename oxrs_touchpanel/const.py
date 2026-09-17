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


def topic_tele(client_id: str) -> str:
    """Telemetry topic for a panel (climate readings)."""
    return f"tele/{client_id}"


def signal_available(client_id: str) -> str:
    """Dispatcher signal fired when a panel's availability changes."""
    return f"{DOMAIN}_{client_id}_available"


def signal_tele(client_id: str) -> str:
    """Dispatcher signal fired when a panel publishes new telemetry."""
    return f"{DOMAIN}_{client_id}_tele"
