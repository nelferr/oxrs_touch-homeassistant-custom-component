"""Tile-type registry: OXRS tile <-> HA entity translation.

Each tile type declares:
  * ``style``  - the OXRS tile style string used in the ``conf/`` payload.
  * ``domain`` - the HA entity domain the tile binds to (drives the picker).
  * ``icon``   - default icon if the user does not choose one.
  * ``build_state`` - build the ``cmnd/`` tile object from the bound entity.
  * ``handle_event`` - apply an incoming ``stat/`` event to the bound entity.

Add a new tile type by adding an entry here; no other files need to change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENTITY_ID,
    CONF_SCREEN,
    CONF_TILE,
    KELVIN_MAX,
    KELVIN_MIN,
)


class TileType(TypedDict):
    """Definition of a supported tile type."""

    style: str
    domain: str | list[str]
    icon: str
    label: str
    config_extra: Callable[[HomeAssistant, dict[str, Any]], dict[str, Any]] | None
    build_state: Callable[[HomeAssistant, dict[str, Any]], dict[str, Any] | None]
    handle_event: Callable[[HomeAssistant, dict[str, Any], dict[str, Any]], Awaitable[None]]


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


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


# ─── buttonSlider → light (brightness as a 0-100 level) ──────────────────────
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


TILE_TYPES: dict[str, TileType] = {
    "cct": TileType(
        style="colorPickerCct",
        domain="light",
        icon="_bulb",
        label="CCT light (colour temperature)",
        config_extra=None,
        build_state=_cct_build_state,
        handle_event=_cct_handle_event,
    ),
    "slider": TileType(
        style="buttonSlider",
        domain="light",
        icon="_bulb",
        label="Slider (light brightness)",
        config_extra=_level_0_100,
        build_state=_slider_build_state,
        handle_event=_slider_handle_event,
    ),
    "updown": TileType(
        style="buttonUpDown",
        domain="cover",
        icon="_blind",
        label="Up/Down buttons (cover)",
        config_extra=_level_0_100,
        build_state=_updown_build_state,
        handle_event=_updown_handle_event,
    ),
    "thermostat": TileType(
        style="thermostat",
        domain="climate",
        icon="_thermostat",
        label="Thermostat (climate)",
        config_extra=_thermostat_arc_range,
        build_state=_thermostat_build_state,
        handle_event=_thermostat_handle_event,
    ),
    "button": TileType(
        style="button",
        domain=["script", "scene", "switch", "button", "input_button"],
        icon="_onoff",
        label="Button (script / scene / switch)",
        config_extra=None,
        build_state=_button_build_state,
        handle_event=_button_handle_event,
    ),
    "volume": TileType(
        style="buttonUpDown",
        domain="media_player",
        icon="_speaker",
        label="Volume up/down (media player)",
        config_extra=_level_0_100,
        build_state=_volume_build_state,
        handle_event=_volume_handle_event,
    ),
    "select": TileType(
        style="dropDown",
        domain=["select", "input_select", "media_player"],
        icon="_music",
        label="Selector list (source / radio / playlist)",
        config_extra=None,
        build_state=_select_build_state,
        handle_event=_select_handle_event,
    ),
}
