"""Constants for the OXRS Touch Panel integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "oxrs_touchpanel"
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]

# Config entry data / options keys
CONF_CLIENT_ID = "client_id"
CONF_NAME = "name"
CONF_TILES = "tiles"

# Per-tile keys (old - kept for backward compatibility)
CONF_SCREEN = "screen"
CONF_TILE = "tile"
CONF_TYPE = "type"
CONF_ENTITY_ID = "entity_id"
CONF_LABEL = "label"
CONF_ICON = "icon"

# Per-tile keys (new - flexible actions)
CONF_ACTIONS = "actions"
CONF_ACTION_SEQUENCE = "sequence"
CONF_ACTION_MODE = "mode"
CONF_ACTION_CONDITIONS = "conditions"
CONF_ACTION_ENTITY = "action_entity"  # Optional entity for display/feedback

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
