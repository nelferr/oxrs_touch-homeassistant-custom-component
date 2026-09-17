"""Tile-type registry: OXRS tile <-> HA entity translation.

Each tile type declares:
  * ``style``  - the OXRS tile style string used in the ``conf/`` payload.
  * ``domain`` - the HA entity domain the tile binds to (drives the picker).
  * ``device_class`` - optional; narrows the picker beyond the domain, so a
    door tile only offers door/window contacts rather than every binary_sensor.
  * ``color_modes`` - optional; light colour modes the tile can drive, used by
    ``eligible_entity_ids`` to keep incapable bulbs out of the picker.
  * ``features`` - optional; ``{domain: bitmask}`` of entity features the tile
    needs, matched any-of. Domains left out of the mapping are not checked.
  * ``numeric_state`` - optional; the tile can only render a numeric value.
  * ``suggested_icons`` - optional; icons offered first in the picker, best
    first. Bundled icons that pair with an "on" partner in ``library.py``
    swap automatically with the tile's state.
  * ``icon``   - default icon if the user does not choose one.
  * ``build_state`` - build the ``cmnd/`` tile object from the bound entity.
  * ``handle_event`` - apply an incoming ``stat/`` event to the bound entity.

Add a new tile type by adding an entry here; no other files need to change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, TypedDict

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENTITY_ID,
    CONF_INDICATOR_SECONDARY_ENTITY_ID,
    CONF_SCREEN,
    CONF_TILE,
    KELVIN_MAX,
    KELVIN_MIN,
)


class TileType(TypedDict):
    """Definition of a supported tile type."""

    style: str
    domain: str | list[str]
    device_class: NotRequired[str | list[str]]
    color_modes: NotRequired[list[str]]
    features: NotRequired[dict[str, int]]
    numeric_state: NotRequired[bool]
    suggested_icons: NotRequired[list[str]]
    icon: str
    label: str
    config_extra: Callable[[HomeAssistant, dict[str, Any]], dict[str, Any]] | None
    build_state: Callable[[HomeAssistant, dict[str, Any]], dict[str, Any] | None]
    handle_event: Callable[[HomeAssistant, dict[str, Any], dict[str, Any]], Awaitable[None]]


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


# HA light colour modes (ColorMode) grouped by what a tile can do with them.
# "onoff" and "unknown" appear in neither: a bulb that only switches can't be
# driven by a colour wheel or a brightness slider.
COLOR_CAPABLE_MODES = ["hs", "xy", "rgb", "rgbw", "rgbww"]
BRIGHTNESS_CAPABLE_MODES = ["brightness", "color_temp", "white", *COLOR_CAPABLE_MODES]


def suggested_icons(definition: TileType, available: set[str]) -> list[str]:
    """A tile type's suggested icons that actually exist, best first.

    Bundled icons can be deleted from the library, and a suggestion that isn't
    there would configure a tile with an icon the panel never receives.
    """
    return [icon for icon in definition.get("suggested_icons", []) if icon in available]


def default_icon(definition: TileType, available: set[str]) -> str:
    """The icon a new tile of this type starts with."""
    return next(iter(suggested_icons(definition, available)), definition["icon"])


def _is_numeric_state(state: Any) -> bool:
    """Whether a sensor carries a number the indicator tile can render.

    A sensor that is momentarily "unknown" still counts if it declares a unit
    or state class, so a thermometer doesn't vanish from the picker between
    readings.
    """
    if state.attributes.get("unit_of_measurement") or state.attributes.get("state_class"):
        return True
    try:
        float(state.state)
    except (TypeError, ValueError):
        return False
    return True


def eligible_entity_ids(hass: HomeAssistant, definition: TileType) -> list[str]:
    """List the entities a tile type can actually drive, for the config picker.

    The entity selector filters on domain and device_class but cannot inspect
    attributes, so everything attribute-shaped is resolved here: light colour
    capability (which HA expresses as ``supported_color_modes`` rather than as
    feature flags), entity feature bitmasks, and whether a sensor is numeric.
    Without this a colour picker will happily bind to a plain on/off bulb, or a
    position slider to a blind that only knows open and closed.

    Unavailable entities drop out at the same time. They are useless on a panel
    and report no capability attributes anyway, so they could not be checked
    even if we wanted to offer them.
    """
    domains = definition["domain"]
    if isinstance(domains, str):
        domains = [domains]
    color_modes = definition.get("color_modes")
    features = definition.get("features")
    numeric_state = definition.get("numeric_state")

    eligible: list[str] = []
    for state in hass.states.async_all(domains):
        if state.state == STATE_UNAVAILABLE:
            continue
        # Each check is scoped to the domain it applies to, so a multi-domain
        # type like updownlevel does not lose its covers for want of a colour
        # mode, nor its lights for want of a cover feature.
        if color_modes and state.domain == "light":
            supported = state.attributes.get("supported_color_modes") or []
            if not any(mode in color_modes for mode in supported):
                continue
        if features:
            required = features.get(state.domain)
            if required and not int(state.attributes.get("supported_features") or 0) & required:
                continue
        if numeric_state and not _is_numeric_state(state):
            continue
        eligible.append(state.entity_id)
    return eligible


# ─── colorPickerCct ──────────────────────────────────────────────────────────
def _cct_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any] | None:
    entity_id = tile[CONF_ENTITY_ID]
    state = hass.states.get(entity_id)
    is_on = state is not None and state.state == "on"
    brightness = 0
    kelvin = 4000
    if state is not None:
        brightness = state.attributes.get("brightness") or 0
        kelvin = state.attributes.get("color_temp_kelvin") or 4000
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "state": "on" if is_on else "off",
        "colorPicker": {
            "colorKelvin": _clamp(int(kelvin), KELVIN_MIN, KELVIN_MAX),
            "brightness": round(int(brightness) / 255 * 100),
        },
    }


async def _cct_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    entity_id = tile[CONF_ENTITY_ID]
    ptype = payload.get("type")
    # Bulb button reports the tile's CURRENT (pre-toggle) state -> toggle.
    if ptype == "button" and payload.get("event") == "single":
        await hass.services.async_call(
            "light", "toggle", {"entity_id": entity_id}, blocking=False
        )
        return
    if ptype == "colorPicker":
        picker_state = payload.get("state") or {}
        data: dict[str, Any] = {"entity_id": entity_id}
        if picker_state.get("colorKelvin"):
            data["color_temp_kelvin"] = int(picker_state["colorKelvin"])
        if "brightness" in picker_state:
            data["brightness_pct"] = int(picker_state["brightness"])
        await hass.services.async_call("light", "turn_on", data, blocking=False)


# ─── colorPickerRgbCct → light (RGBW: RGB color + white channel + brightness) ────
def _rgbw_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any] | None:
    """Build OXRS RGBW tile state from HA light entity.
    
    Sends to panel:
    - colorRgb: {r, g, b} extracted from rgbw_color
    - brightness: 0-100
    - state: on/off
    """
    entity_id = tile[CONF_ENTITY_ID]
    state = hass.states.get(entity_id)
    is_on = state is not None and state.state == "on"
    brightness = 0
    r, g, b = 255, 255, 255
    
    if state is not None:
        brightness = state.attributes.get("brightness") or 0
        # Get RGBW color - use rgbw_color first, fallback to values
        rgbw_color = state.attributes.get("rgbw_color")
        if rgbw_color and isinstance(rgbw_color, (list, tuple)) and len(rgbw_color) >= 3:
            r, g, b = rgbw_color[0], rgbw_color[1], rgbw_color[2]
        else:
            # Fallback to values attribute if rgbw_color not set
            values = state.attributes.get("values")
            if values and isinstance(values, (list, tuple)) and len(values) >= 3:
                r, g, b = values[0], values[1], values[2]
    
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "state": "on" if is_on else "off",
        "colorPicker": {
            "colorRgb": {  # Send as colorRgb (not rgbw) to match OXRS format
                "r": int(r),
                "g": int(g),
                "b": int(b),
            },
            "brightness": round(int(brightness) / 255 * 100),
        },
    }


async def _rgbw_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Process OXRS RGBW tile event and apply to HA light entity.
    
    OXRS colorPickerRgbCct sends:
    - colorRgb: {r, g, b} (RGB only, no W)
    - colorKelvin: always 0 for RGBW
    - brightness: 0 if not adjusted
    
    We need to:
    - Extract R, G, B from colorRgb
    - Preserve W (white) from current light state
    - Only override brightness if non-zero
    """
    entity_id = tile[CONF_ENTITY_ID]
    ptype = payload.get("type")
    
    # Button press: toggle
    if ptype == "button" and payload.get("event") == "single":
        await hass.services.async_call(
            "light", "toggle", {"entity_id": entity_id}, blocking=False
        )
        return
    
    # Color picker: RGB from panel + preserve white channel from current state
    if ptype == "colorPicker":
        picker_state = payload.get("state") or {}
        data: dict[str, Any] = {"entity_id": entity_id}
        
        # Get current light state to preserve white channel
        current_state = hass.states.get(entity_id)
        current_w = 0
        if current_state:
            current_rgbw = current_state.attributes.get("rgbw_color")
            if current_rgbw and len(current_rgbw) >= 4:
                current_w = current_rgbw[3]  # Get white value from current state
        
        # Extract RGB color from OXRS payload (colorRgb, not rgbw)
        color_rgb = picker_state.get("colorRgb")
        if color_rgb:
            r = int(color_rgb.get("r", 255))
            g = int(color_rgb.get("g", 255))
            b = int(color_rgb.get("b", 255))
            # Preserve the white channel from current state
            data["rgbw_color"] = [r, g, b, current_w]
        
        # Only set brightness if it's non-zero (avoid turning off light)
        brightness = picker_state.get("brightness", 0)
        if brightness > 0:
            data["brightness_pct"] = int(brightness)
        
        await hass.services.async_call("light", "turn_on", data, blocking=False)
def _level_0_100(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    return {"levelBottom": 0, "levelTop": 100}


def _slider_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    entity_id = tile[CONF_ENTITY_ID]
    state = hass.states.get(entity_id)
    is_on = state is not None and state.state == "on"
    brightness = 0
    if state is not None:
        brightness = state.attributes.get("brightness") or 0
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "state": "on" if is_on else "off",
        "level": round(int(brightness) / 255 * 100),
    }


async def _slider_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    entity_id = tile[CONF_ENTITY_ID]
    ptype = payload.get("type")
    if ptype == "level" and payload.get("event") == "slide":
        pct = int(payload.get("state") or 0)
        if pct <= 0:
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": entity_id}, blocking=False
            )
        else:
            await hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": entity_id, "brightness_pct": pct},
                blocking=False,
            )
    elif ptype == "button" and payload.get("event") == "single":
        await hass.services.async_call(
            "light", "toggle", {"entity_id": entity_id}, blocking=False
        )


# ─── buttonUpDown → cover (up=open, down=close, hold=stop) ───────────────────
def _updown_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    entity_id = tile[CONF_ENTITY_ID]
    state = hass.states.get(entity_id)
    position = None
    if state is not None:
        position = state.attributes.get("current_position")
        if position is None:
            position = 100 if state.state == "open" else 0
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "state": "on" if state is not None and state.state != "closed" else "off",
        "level": int(position or 0),
    }


async def _updown_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    entity_id = tile[CONF_ENTITY_ID]
    ptype = payload.get("type")
    state = hass.states.get(entity_id)
    moving = state is not None and state.state in ("opening", "closing")
    # A hold, or any tap while the cover is moving, stops it mid-travel.
    if payload.get("event") == "hold" or moving:
        await hass.services.async_call(
            "cover", "stop_cover", {"entity_id": entity_id}, blocking=False
        )
        return
    if ptype == "up":
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": entity_id}, blocking=False
        )
    elif ptype == "down":
        await hass.services.async_call(
            "cover", "close_cover", {"entity_id": entity_id}, blocking=False
        )


# ─── thermostat → climate (temperatures in tenths of a degree) ──────────────
def _thermostat_arc_range(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    state = hass.states.get(tile[CONF_ENTITY_ID])
    min_temp = state.attributes.get("min_temp", 7.0) if state else 7.0
    max_temp = state.attributes.get("max_temp", 35.0) if state else 35.0
    return {"levelBottom": round(float(min_temp) * 10), "levelTop": round(float(max_temp) * 10)}


def _thermostat_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    state = hass.states.get(tile[CONF_ENTITY_ID])
    current = 0.0
    target = 0.0
    modes: list[str] = []
    mode_index = 1
    if state is not None:
        current = state.attributes.get("current_temperature") or 0.0
        target = state.attributes.get("temperature") or 0.0
        modes = [str(m) for m in (state.attributes.get("hvac_modes") or [])]
        if state.state in modes:
            mode_index = modes.index(state.state) + 1
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "thermostat": {
            "currentTemperature": round(float(current) * 10),
            "targetTemperature": round(float(target) * 10),
            "units": "°C",
            "modeList": modes,
            "mode": mode_index,
        },
    }


async def _thermostat_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    entity_id = tile[CONF_ENTITY_ID]
    event_state = payload.get("state") or {}
    if "targetTemperature" in event_state:
        temperature = int(event_state["targetTemperature"]) / 10
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": temperature},
            blocking=False,
        )
    if "mode" in event_state:
        state = hass.states.get(entity_id)
        modes = [str(m) for m in (state.attributes.get("hvac_modes") or [])] if state else []
        index = int(event_state["mode"]) - 1
        if 0 <= index < len(modes):
            await hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": entity_id, "hvac_mode": modes[index]},
                blocking=False,
            )


# ─── button → script / scene / switch / button / input_button ──────────────
_BUTTON_SERVICES = {
    "switch": ("switch", "toggle"),
    "script": ("script", "turn_on"),
    "scene": ("scene", "turn_on"),
    "button": ("button", "press"),
    "input_button": ("input_button", "press"),
}


def _button_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    state = hass.states.get(tile[CONF_ENTITY_ID])
    # Switches (and running scripts) report 'on'; momentary targets stay 'off'.
    is_on = state is not None and state.state == "on"
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "state": "on" if is_on else "off",
    }


async def _button_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    if payload.get("type") != "button" or payload.get("event") != "single":
        return
    entity_id = tile[CONF_ENTITY_ID]
    domain = entity_id.split(".")[0]
    service = _BUTTON_SERVICES.get(domain)
    if service is not None:
        await hass.services.async_call(
            service[0], service[1], {"entity_id": entity_id}, blocking=False
        )


# ─── volume → media_player (buttonUpDown = volume up/down) ────────────────
def _volume_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    state = hass.states.get(tile[CONF_ENTITY_ID])
    level = 0
    if state is not None:
        level = round((state.attributes.get("volume_level") or 0) * 100)
    is_on = state is not None and state.state in (
        "playing",
        "paused",
        "buffering",
        "idle",
        "on",
    )
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "state": "on" if is_on else "off",
        "level": level,
    }


async def _volume_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    entity_id = tile[CONF_ENTITY_ID]
    ptype = payload.get("type")
    # single or hold (repeat) both step the volume for a smooth ramp.
    if ptype == "up":
        await hass.services.async_call(
            "media_player", "volume_up", {"entity_id": entity_id}, blocking=False
        )
    elif ptype == "down":
        await hass.services.async_call(
            "media_player", "volume_down", {"entity_id": entity_id}, blocking=False
        )


# ─── dropDown → select / input_select / media_player source ───────────────
def _select_options(state: Any, domain: str) -> list[str]:
    if domain == "media_player":
        return list(state.attributes.get("source_list") or [])
    return list(state.attributes.get("options") or [])


def _select_current(state: Any, domain: str) -> str | None:
    if domain == "media_player":
        return state.attributes.get("source")
    return state.state


def _select_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    entity_id = tile[CONF_ENTITY_ID]
    domain = entity_id.split(".")[0]
    state = hass.states.get(entity_id)
    options: list[str] = []
    index = 0
    if state is not None:
        options = _select_options(state, domain)
        current = _select_current(state, domain)
        if current in options:
            index = options.index(current) + 1
    # dropDownList populates the popup; dropDownSelect is 1-based (0 = none).
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "dropDownList": options,
        "dropDownSelect": index,
    }


async def _select_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    if payload.get("type") != "dropDown" or payload.get("event") != "selection":
        return
    entity_id = tile[CONF_ENTITY_ID]
    domain = entity_id.split(".")[0]
    state = hass.states.get(entity_id)
    if state is None:
        return
    options = _select_options(state, domain)
    index = int(payload.get("state") or 0) - 1
    if not 0 <= index < len(options):
        return
    option = options[index]
    if domain == "media_player":
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": entity_id, "source": option},
            blocking=False,
        )
    else:
        await hass.services.async_call(
            domain,
            "select_option",
            {"entity_id": entity_id, "option": option},
            blocking=False,
        )


# ─── buttonUpDownLevel → cover position OR light brightness, with the panel
#     tracking the level internally (firmware sends back the resulting value
#     after every up/down tap or hold, rather than just a direction) ─────────
def _updownlevel_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    entity_id = tile[CONF_ENTITY_ID]
    domain = entity_id.split(".")[0]
    state = hass.states.get(entity_id)

    if domain == "cover":
        position = None
        if state is not None:
            position = state.attributes.get("current_position")
            if position is None:
                position = 100 if state.state == "open" else 0
        is_on = state is not None and state.state != "closed"
        level = int(position or 0)
    else:  # light
        brightness = 0
        if state is not None:
            brightness = state.attributes.get("brightness") or 0
        is_on = state is not None and state.state == "on"
        level = round(int(brightness) / 255 * 100)

    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "state": "on" if is_on else "off",
        "level": level,
    }


async def _updownlevel_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Apply a buttonUpDownLevel event.

    The panel tracks the level internally and reports the NEW absolute value
    in "state" after every tap/hold (type: "level", event: "up"|"down").
    A plain tap on the tile itself (type: "button") toggles fully open/closed
    or on/off, matching the other button-family tiles.
    """
    entity_id = tile[CONF_ENTITY_ID]
    domain = entity_id.split(".")[0]
    ptype = payload.get("type")

    if ptype == "button" and payload.get("event") == "single":
        if domain == "cover":
            state = hass.states.get(entity_id)
            if state is not None and state.state == "closed":
                await hass.services.async_call(
                    "cover", "open_cover", {"entity_id": entity_id}, blocking=False
                )
            else:
                await hass.services.async_call(
                    "cover", "close_cover", {"entity_id": entity_id}, blocking=False
                )
        else:
            await hass.services.async_call(
                "light", "toggle", {"entity_id": entity_id}, blocking=False
            )
        return

    if ptype == "level" and payload.get("event") in ("up", "down"):
        level = _clamp(int(payload.get("state") or 0), 0, 100)
        if domain == "cover":
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": entity_id, "position": level},
                blocking=False,
            )
        else:
            if level <= 0:
                await hass.services.async_call(
                    "light", "turn_off", {"entity_id": entity_id}, blocking=False
                )
            else:
                await hass.services.async_call(
                    "light",
                    "turn_on",
                    {"entity_id": entity_id, "brightness_pct": level},
                    blocking=False,
                )


# ─── indicator → sensor (display-only, no touch interaction) ────────────────
_INDICATOR_ALLOWED_CHARS = set("0123456789+-.:")


def _format_indicator_value(raw_state: str) -> str:
    """Format a sensor state for the OXRS indicator tile.

    OXRS restricts the "value" field to the characters 0-9 + - . : — try to
    render numeric sensor values with one decimal place, and otherwise strip
    anything the firmware would reject.
    """
    try:
        return f"{float(raw_state):.1f}"
    except (TypeError, ValueError):
        return "".join(c for c in raw_state if c in _INDICATOR_ALLOWED_CHARS)


def _indicator_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    entity_id = tile[CONF_ENTITY_ID]
    state = hass.states.get(entity_id)
    value = ""
    units = ""
    if state is not None:
        value = _format_indicator_value(state.state)
        units = state.attributes.get("unit_of_measurement") or ""

    number: dict[str, Any] = {"value": value, "units": units}

    secondary_id = tile.get(CONF_INDICATOR_SECONDARY_ENTITY_ID)
    if secondary_id:
        secondary_state = hass.states.get(secondary_id)
        if secondary_state is not None:
            number["subValue"] = _format_indicator_value(secondary_state.state)
            number["subUnits"] = secondary_state.attributes.get("unit_of_measurement") or ""

    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "number": number,
    }


async def _display_only_handle_event(
    hass: HomeAssistant, tile: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Swallow touch events for tiles that only report, never control.

    The indicator style receives no touch events from the firmware at all.
    The button style does, so the door/window, presence and text tiles below
    reuse this to stay read-only despite being tappable.
    """
    return


# ─── read-only binary_sensor tiles (doors / windows / presence) ─────────────
def _binary_display_builder(
    on_text: str, off_text: str
) -> Callable[[HomeAssistant, dict[str, Any]], dict[str, Any]]:
    """Build a display-only ``build_state`` for an on/off sensor.

    The tile's lit/unlit state carries the reading at a glance; the subLabel
    spells it out. A subLabel source picked in the config flow still wins -
    ``_augment_tile_state`` applies it after this returns.
    """

    def build(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
        state = hass.states.get(tile[CONF_ENTITY_ID])
        is_on = state is not None and state.state == "on"
        return {
            "screen": tile[CONF_SCREEN],
            "tile": tile[CONF_TILE],
            "state": "on" if is_on else "off",
            "subLabel": on_text if is_on else off_text,
        }

    return build


# ─── text → sensor (state rendered as text in place of the icon) ────────────
def _text_build_state(hass: HomeAssistant, tile: dict[str, Any]) -> dict[str, Any]:
    """Render a sensor's state as tile text.

    This is the escape hatch from the indicator style, whose "value" field the
    firmware restricts to 0-9 + - . : — no use for states like "Home" or
    "Disconnected". A non-empty "text" hides the icon and shows the text
    instead; an empty one restores the icon, which is the right fallback when
    the entity is missing.
    """
    state = hass.states.get(tile[CONF_ENTITY_ID])
    return {
        "screen": tile[CONF_SCREEN],
        "tile": tile[CONF_TILE],
        "text": "" if state is None else state.state,
    }


TILE_TYPES: dict[str, TileType] = {
    "rgbw": TileType(
        style="colorPickerRgbCct",
        domain="light",
        color_modes=COLOR_CAPABLE_MODES,
        icon="_bulb",
        label="RGBW light (RGB + white channel)",
        config_extra=None,
        build_state=_rgbw_build_state,
        handle_event=_rgbw_handle_event,
    ),
    "cct": TileType(
        style="colorPickerCct",
        domain="light",
        color_modes=["color_temp"],
        icon="_bulb",
        label="CCT light (colour temperature)",
        config_extra=None,
        build_state=_cct_build_state,
        handle_event=_cct_handle_event,
    ),
    "slider": TileType(
        style="buttonSlider",
        domain="light",
        color_modes=BRIGHTNESS_CAPABLE_MODES,
        icon="_bulb",
        label="Slider (light brightness)",
        config_extra=_level_0_100,
        build_state=_slider_build_state,
        handle_event=_slider_handle_event,
    ),
    "updown": TileType(
        style="buttonUpDown",
        domain="cover",
        # Stop is used on hold, but only when the cover reports it, so it is
        # not required here - open or close alone still makes a usable tile.
        features={"cover": CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE},
        suggested_icons=["shutter", "curtain", "_blind"],
        icon="_blind",
        label="Up/Down buttons (cover)",
        config_extra=_level_0_100,
        build_state=_updown_build_state,
        handle_event=_updown_handle_event,
    ),
    "thermostat": TileType(
        style="thermostat",
        domain="climate",
        # The tile sets a single setpoint. A climate entity that only does
        # TARGET_TEMPERATURE_RANGE (separate heat/cool) would ignore it.
        features={"climate": ClimateEntityFeature.TARGET_TEMPERATURE},
        suggested_icons=["_thermostat", "ac", "heat", "cool"],
        icon="_thermostat",
        label="Thermostat (climate)",
        config_extra=_thermostat_arc_range,
        build_state=_thermostat_build_state,
        handle_event=_thermostat_handle_event,
    ),
    "button": TileType(
        style="button",
        domain=["script", "scene", "switch", "button", "input_button"],
        suggested_icons=["_onoff", "scene", "script", "automation", "fan-off"],
        icon="_onoff",
        label="Button (script / scene / switch)",
        config_extra=None,
        build_state=_button_build_state,
        handle_event=_button_handle_event,
    ),
    "volume": TileType(
        style="buttonUpDown",
        domain="media_player",
        # VOLUME_SET counts too: HA's default volume_up/volume_down step the
        # level for any player that can set it outright.
        features={
            "media_player": (
                MediaPlayerEntityFeature.VOLUME_STEP
                | MediaPlayerEntityFeature.VOLUME_SET
            )
        },
        suggested_icons=["_speaker", "volume", "tv"],
        icon="_speaker",
        label="Volume up/down (media player)",
        config_extra=_level_0_100,
        build_state=_volume_build_state,
        handle_event=_volume_handle_event,
    ),
    "select": TileType(
        style="dropDown",
        domain=["select", "input_select", "media_player"],
        # A media player is only listed if it has sources to choose from;
        # select and input_select always do, so they are not checked.
        features={"media_player": MediaPlayerEntityFeature.SELECT_SOURCE},
        suggested_icons=["_music", "list", "playlist", "tv"],
        icon="_music",
        label="Selector list (source / radio / playlist)",
        config_extra=None,
        build_state=_select_build_state,
        handle_event=_select_handle_event,
    ),
    "updownlevel": TileType(
        style="buttonUpDownLevel",
        domain=["cover", "light"],
        color_modes=BRIGHTNESS_CAPABLE_MODES,
        # This tile drives an absolute position, so a blind that only knows
        # open and closed can't honour it.
        features={"cover": CoverEntityFeature.SET_POSITION},
        # Built-in first: this type also takes lights, and a shutter default
        # would be wrong for them.
        suggested_icons=["_blind", "shutter", "curtain", "_bulb"],
        icon="_blind",
        label="Up/Down with level (cover position / light brightness)",
        config_extra=_level_0_100,
        build_state=_updownlevel_build_state,
        handle_event=_updownlevel_handle_event,
    ),
    "indicator": TileType(
        style="indicator",
        domain="sensor",
        # The firmware restricts this tile's value to 0-9 + - . : so a text
        # sensor would render as stripped nonsense. Use the "text" type.
        numeric_state=True,
        icon="_thermostat",
        label="Sensor display (temperature, humidity, etc.)",
        config_extra=None,
        build_state=_indicator_build_state,
        handle_event=_display_only_handle_event,
    ),
    # Read-only tiles. They use the button style because it lights up with
    # "state" and can show text, which indicator cannot — but they send no
    # service calls back, so tapping them does nothing.
    "door_window": TileType(
        style="button",
        domain="binary_sensor",
        device_class=["door", "window", "garage_door", "opening"],
        suggested_icons=["door-closed", "window-closed", "garage", "gate", "_door", "_window"],
        icon="_door",
        label="Door / window contact (read-only)",
        config_extra=None,
        build_state=_binary_display_builder("Open", "Closed"),
        handle_event=_display_only_handle_event,
    ),
    "presence": TileType(
        style="button",
        domain="binary_sensor",
        device_class=["motion", "occupancy", "presence"],
        suggested_icons=["motion", "presence-home", "_onoff"],
        icon="_onoff",
        label="Presence / motion (read-only)",
        config_extra=None,
        build_state=_binary_display_builder("Detected", "Clear"),
        handle_event=_display_only_handle_event,
    ),
    "text": TileType(
        style="button",
        domain=["sensor", "binary_sensor"],
        icon="_onoff",
        label="Text display (sensor state as text)",
        config_extra=None,
        build_state=_text_build_state,
        handle_event=_display_only_handle_event,
    ),
}
