"""Generate flexible action sequences based on entity type and OXRS tile configuration.

This module creates action sequences that translate OXRS MQTT payloads into
Home Assistant service calls. For example:

  Entity: light.bedroom (with color_temp_kelvin)
  Generated action:
    - service: light.turn_on
      data:
        entity_id: light.bedroom
        brightness_pct: "{{ payload.state.brightness }}"
        color_temp_kelvin: "{{ payload.state.colorKelvin }}"

The generated sequences use template variables from MQTT payloads so the
OXRS panel UI controls work directly with Home Assistant.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _get_tile_type_for_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """Determine the best tile type for an entity based on its domain and attributes."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    
    domain = entity_id.split(".")[0]
    
    if domain == "light":
        # Check if light supports color temperature
        if "color_temp_kelvin" in state.attributes:
            return "cct"  # Color picker with color temp
        elif "brightness" in state.attributes:
            return "slider"  # Simple brightness slider
        else:
            return "button"  # On/off only
    
    elif domain == "cover":
        return "updown"  # Up/down open/close
    
    elif domain == "climate":
        return "thermostat"  # Thermostat with temp + mode
    
    elif domain == "media_player":
        # Check if it has source list
        if state.attributes.get("source_list"):
            return "select"  # Source selector
        else:
            return "volume"  # Volume up/down
    
    elif domain in ("switch", "script", "scene", "button", "input_button"):
        return "button"  # Generic button
    
    elif domain in ("select", "input_select"):
        return "select"  # Dropdown selector
    
    return None


def generate_action_sequence(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]] | None:
    """Generate an action sequence for an entity based on its type.
    
    Args:
        hass: Home Assistant instance
        entity_id: Entity to generate actions for
        
    Returns:
        List of action dicts, or None if entity type not supported
    """
    tile_type = _get_tile_type_for_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    
    if not tile_type or not state:
        _LOGGER.warning(f"Could not generate action sequence for {entity_id}")
        return None
    
    domain = entity_id.split(".")[0]
    
    # ─── CCT Light (colorPickerCct) ───────────────────────────────────────
    if tile_type == "cct":
        return [
            {
                "service": "light.turn_on",
                "data": {
                    "entity_id": entity_id,
                    "brightness_pct": "{{ payload.state.brightness }}",
                    "color_temp_kelvin": "{{ payload.state.colorKelvin }}",
                },
            },
            {
                "service": "light.toggle",
                "data": {"entity_id": entity_id},
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ payload.type == 'button' }}",
                    }
                ],
            },
        ]
    
    # ─── Brightness Slider (buttonSlider) ─────────────────────────────────
    elif tile_type == "slider":
        return [
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": "{{ payload.state <= 0 }}",
                            }
                        ],
                        "sequence": [
                            {
                                "service": "light.turn_off",
                                "data": {"entity_id": entity_id},
                            }
                        ],
                    },
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": "{{ payload.event == 'slide' }}",
                            }
                        ],
                        "sequence": [
                            {
                                "service": "light.turn_on",
                                "data": {
                                    "entity_id": entity_id,
                                    "brightness_pct": "{{ payload.state }}",
                                },
                            }
                        ],
                    },
                ]
            },
            {
                "service": "light.toggle",
                "data": {"entity_id": entity_id},
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ payload.type == 'button' }}",
                    }
                ],
            },
        ]
    
    # ─── Up/Down Cover (buttonUpDown) ────────────────────────────────────
    elif tile_type == "updown":
        return [
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": "{{ payload.type == 'up' }}",
                            }
                        ],
                        "sequence": [
                            {
                                "service": "cover.open_cover",
                                "data": {"entity_id": entity_id},
                            }
                        ],
                    },
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": "{{ payload.type == 'down' }}",
                            }
                        ],
                        "sequence": [
                            {
                                "service": "cover.close_cover",
                                "data": {"entity_id": entity_id},
                            }
                        ],
                    },
                ]
            },
            {
                "service": "cover.stop_cover",
                "data": {"entity_id": entity_id},
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ payload.event == 'hold' }}",
                    }
                ],
            },
        ]
    
    # ─── Thermostat (thermostat) ──────────────────────────────────────────
    elif tile_type == "thermostat":
        modes_str = ", ".join(state.attributes.get("hvac_modes", []))
        return [
            {
                "service": "climate.set_temperature",
                "data": {
                    "entity_id": entity_id,
                    "temperature": "{{ (payload.state.targetTemperature | int(0)) / 10 }}",
                },
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ 'targetTemperature' in payload.state }}",
                    }
                ],
            },
            {
                "service": "climate.set_hvac_mode",
                "data": {
                    "entity_id": entity_id,
                    "hvac_mode": "{{ payload.state.get('mode_name', 'heat') }}",
                },
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ 'mode' in payload.state }}",
                    }
                ],
            },
        ]
    
    # ─── Button (script/scene/switch) ────────────────────────────────────
    elif tile_type == "button":
        if domain == "switch":
            service = ("switch", "toggle")
        elif domain == "script":
            service = ("script", "turn_on")
        elif domain == "scene":
            service = ("scene", "turn_on")
        elif domain in ("button", "input_button"):
            service = (domain, "press")
        else:
            return None
        
        return [
            {
                "service": f"{service[0]}.{service[1]}",
                "data": {"entity_id": entity_id},
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ payload.event == 'single' }}",
                    }
                ],
            }
        ]
    
    # ─── Volume (media_player) ────────────────────────────────────────────
    elif tile_type == "volume":
        return [
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": "{{ payload.type == 'up' }}",
                            }
                        ],
                        "sequence": [
                            {
                                "service": "media_player.volume_up",
                                "data": {"entity_id": entity_id},
                            }
                        ],
                    },
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": "{{ payload.type == 'down' }}",
                            }
                        ],
                        "sequence": [
                            {
                                "service": "media_player.volume_down",
                                "data": {"entity_id": entity_id},
                            }
                        ],
                    },
                ]
            }
        ]
    
    # ─── Selector (select / input_select / media_player source) ──────────
    elif tile_type == "select":
        if domain == "media_player":
            return [
                {
                    "service": "media_player.select_source",
                    "data": {
                        "entity_id": entity_id,
                        "source": "{{ payload.state.get('selected_option', '') }}",
                    },
                    "conditions": [
                        {
                            "condition": "template",
                            "value_template": "{{ payload.event == 'selection' }}",
                        }
                    ],
                }
            ]
        else:
            return [
                {
                    "service": f"{domain}.select_option",
                    "data": {
                        "entity_id": entity_id,
                        "option": "{{ payload.state.get('selected_option', '') }}",
                    },
                    "conditions": [
                        {
                            "condition": "template",
                            "value_template": "{{ payload.event == 'selection' }}",
                        }
                    ],
                }
            ]
    
    return None
