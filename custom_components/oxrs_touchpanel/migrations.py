"""Migration utilities for converting old tile configs to flexible actions format."""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_ACTION_MODE,
    CONF_ACTION_SEQUENCE,
    CONF_ACTIONS,
    CONF_ENTITY_ID,
    CONF_ICON,
    CONF_LABEL,
    CONF_SCREEN,
    CONF_TILE,
    CONF_TYPE,
)


def has_actions(tile: dict[str, Any]) -> bool:
    """Check if tile uses new flexible actions format."""
    return CONF_ACTIONS in tile


def has_entity_binding(tile: dict[str, Any]) -> bool:
    """Check if tile uses old hardcoded tile type format."""
    return CONF_ENTITY_ID in tile and CONF_TYPE in tile


def migrate_tile_to_actions(tile: dict[str, Any]) -> dict[str, Any]:
    """Convert old tile-based config to action-based.
    
    Maps old tile types to action sequences that produce equivalent behavior.
    """
    tile_type = tile.get(CONF_TYPE)
    entity_id = tile.get(CONF_ENTITY_ID)
    
    # Service mapping: tile type -> (domain, service) for buttons
    service_map = {
        "button": ("button", "press"),
        "scene": ("scene", "turn_on"),
        "script": ("script", "turn_on"),
        "switch": ("switch", "toggle"),
        "input_button": ("input_button", "press"),
    }
    
    # Extract domain from entity_id
    domain = entity_id.split(".")[0] if entity_id else None
    domain_map = {
        "light": "light",
        "switch": "switch",
        "cover": "cover",
        "climate": "climate",
        "media_player": "media_player",
        "select": "select",
        "input_select": "input_select",
        "scene": "scene",
        "script": "script",
        "button": "button",
        "input_button": "input_button",
    }
    
    service_domain = domain_map.get(domain, domain)
    
    # Map tile type to action sequence
    migrated_tile = {
        CONF_SCREEN: tile[CONF_SCREEN],
        CONF_TILE: tile[CONF_TILE],
        CONF_LABEL: tile.get(CONF_LABEL, ""),
        CONF_ICON: tile.get(CONF_ICON, ""),
    }
    
    # Add actions based on tile type
    if tile_type == "cct":
        # CCT light with color temp + brightness
        migrated_tile[CONF_ACTIONS] = [
            {
                CONF_ACTION_MODE: "single",
                CONF_ACTION_SEQUENCE: [
                    {
                        "service": "light.turn_on",
                        "data": {
                            "entity_id": entity_id,
                            "brightness_pct": "{{ brightness }}",
                            "color_temp_kelvin": "{{ kelvin }}",
                        },
                    }
                ],
            }
        ]
    elif tile_type == "slider":
        # Brightness slider
        migrated_tile[CONF_ACTIONS] = [
            {
                CONF_ACTION_MODE: "single",
                CONF_ACTION_SEQUENCE: [
                    {
                        "service": "light.turn_on",
                        "data": {
                            "entity_id": entity_id,
                            "brightness_pct": "{{ level }}",
                        },
                    }
                ],
            }
        ]
    elif tile_type == "updown":
        # Cover or volume up/down
        if service_domain == "cover":
            migrated_tile[CONF_ACTIONS] = [
                {
                    CONF_ACTION_MODE: "single",
                    CONF_ACTION_SEQUENCE: [
                        {
                            "service": "cover.open_cover",
                            "data": {"entity_id": entity_id},
                        }
                    ],
                },
                {
                    CONF_ACTION_MODE: "single",
                    CONF_ACTION_SEQUENCE: [
                        {
                            "service": "cover.close_cover",
                            "data": {"entity_id": entity_id},
                        }
                    ],
                },
            ]
        elif service_domain == "media_player":
            migrated_tile[CONF_ACTIONS] = [
                {
                    CONF_ACTION_MODE: "single",
                    CONF_ACTION_SEQUENCE: [
                        {
                            "service": "media_player.volume_up",
                            "data": {"entity_id": entity_id},
                        }
                    ],
                }
            ]
    elif tile_type == "thermostat":
        # Climate control
        migrated_tile[CONF_ACTIONS] = [
            {
                CONF_ACTION_MODE: "single",
                CONF_ACTION_SEQUENCE: [
                    {
                        "service": "climate.set_temperature",
                        "data": {
                            "entity_id": entity_id,
                            "temperature": "{{ temperature }}",
                        },
                    }
                ],
            }
        ]
    elif tile_type == "button":
        # Generic button - toggle/press
        service = service_map.get(domain, ("switch", "toggle"))
        migrated_tile[CONF_ACTIONS] = [
            {
                CONF_ACTION_MODE: "single",
                CONF_ACTION_SEQUENCE: [
                    {
                        "service": f"{service[0]}.{service[1]}",
                        "data": {"entity_id": entity_id},
                    }
                ],
            }
        ]
    elif tile_type == "select":
        # Dropdown selector
        migrated_tile[CONF_ACTIONS] = [
            {
                CONF_ACTION_MODE: "single",
                CONF_ACTION_SEQUENCE: [
                    {
                        "service": f"{service_domain}.select_option",
                        "data": {
                            "entity_id": entity_id,
                            "option": "{{ selected }}",
                        },
                    }
                ],
            }
        ]
    else:
        # Fallback: generic toggle
        migrated_tile[CONF_ACTIONS] = [
            {
                CONF_ACTION_MODE: "single",
                CONF_ACTION_SEQUENCE: [
                    {
                        "service": "switch.toggle",
                        "data": {"entity_id": entity_id},
                    }
                ],
            }
        ]
    
    return migrated_tile


def ensure_actions_format(tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Migrate all tiles to use actions format if they don't already.
    
    Keeps tiles that already use actions unchanged.
    Converts old tile-type tiles to action-based.
    """
    result = []
    for tile in tiles:
        if has_actions(tile):
            # Already in new format
            result.append(tile)
        elif has_entity_binding(tile):
            # Old format - migrate it
            result.append(migrate_tile_to_actions(tile))
        else:
            # Malformed? Keep as-is
            result.append(tile)
    return result
