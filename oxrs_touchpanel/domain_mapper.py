"""Map Home Assistant entity domains to OXRS tile types.

This module determines which OXRS tile type best fits a given HA entity domain
and attributes. For example:
  - light with color_temp_kelvin → "cct" (color picker)
  - light without color_temp → "slider" (brightness)
  - cover → "updown" (open/close/stop)
  - climate → "thermostat"
  - media_player → "volume" or "select"
  - etc.

Once the tile type is determined, we use the tile type's existing handlers:
  - build_state(): Convert HA entity state → OXRS cmnd/ payload
  - handle_event(): Convert OXRS stat/ payload → HA service call
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


# Domain to tile type mapping
# Maps HA entity domain to best-fit OXRS tile type
DOMAIN_TO_TILE_TYPE: dict[str, str] = {
    # Light domain can map to cct, slider, or button depending on capabilities
    "light": "cct",  # Default, can be overridden by attributes
    "cover": "updown",
    "climate": "thermostat",
    "media_player": "volume",  # Default, can be overridden
    "switch": "button",
    "scene": "button",
    "script": "button",
    "button": "button",
    "input_button": "button",
    "select": "select",
    "input_select": "select",
}


def get_all_tile_types_for_entity(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Get ALL possible OXRS tile types for a given entity.
    
    Unlike get_best_tile_type_for_entity which returns ONE best option,
    this returns ALL valid options so user can choose. For example:
      light with color_temp → ["cct", "slider", "button"]
      light without color_temp → ["slider", "button"]
      cover → ["updown"]
      media_player with sources → ["select", "volume"]
      
    Args:
        hass: Home Assistant instance
        entity_id: Full entity ID
        
    Returns:
        List of possible tile type names (ordered by preference)
    """
    domain = entity_id.split(".")[0]
    state = hass.states.get(entity_id)
    
    if state is None:
        _LOGGER.warning(f"Entity {entity_id} not found in state")
        return []
    
    _LOGGER.debug(f"get_all_tile_types_for_entity({entity_id}), domain={domain}")
    options = []
    
    if domain == "light":
        # All lights can be controlled as button (on/off only)
        options.append("button")
        
        # Brightness support → can use slider
        if "brightness" in state.attributes:
            options.insert(0, "slider")  # Prefer slider over button
        
        # Color temp support → can use CCT (best option)
        if "color_temp_kelvin" in state.attributes or "color_temp" in state.attributes:
            options.insert(0, "cct")  # Prefer CCT over slider over button
        
        _LOGGER.debug(f"  → light options: {options}")
        return options
    
    elif domain == "cover":
        _LOGGER.debug(f"  → cover options: ['updown']")
        return ["updown"]
    
    elif domain == "climate":
        _LOGGER.debug(f"  → climate options: ['thermostat']")
        return ["thermostat"]
    
    elif domain == "media_player":
        options = []
        # Check if has source list
        source_list = state.attributes.get("source_list")
        if source_list:
            options.append("select")  # Source selector
        # Volume control usually available
        options.append("volume")
        _LOGGER.debug(f"  → media_player options: {options}")
        return options
    
    elif domain in ("switch", "script", "scene", "button", "input_button"):
        _LOGGER.debug(f"  → {domain} options: ['button']")
        return ["button"]
    
    elif domain in ("select", "input_select"):
        _LOGGER.debug(f"  → {domain} options: ['select']")
        return ["select"]
    
    _LOGGER.warning(f"No tile type options for domain: {domain}")
    return []


def get_best_tile_type_for_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """Determine the best OXRS tile type for a given entity.
    
    Args:
        hass: Home Assistant instance
        entity_id: Full entity ID (e.g., "light.bedroom")
        
    Returns:
        Tile type name ("cct", "slider", "updown", etc.), or None if unsupported
    """
    domain = entity_id.split(".")[0]
    state = hass.states.get(entity_id)
    
    if state is None:
        _LOGGER.warning(f"Entity {entity_id} not found in state")
        return None
    
    _LOGGER.debug(f"get_best_tile_type_for_entity({entity_id}), domain={domain}")
    
    # Special handling for lights - check attributes
    if domain == "light":
        # Color temp support → use CCT tile
        if "color_temp_kelvin" in state.attributes or "color_temp" in state.attributes:
            _LOGGER.debug(f"  → light has color_temp, returning 'cct'")
            return "cct"
        # Brightness support → use slider
        elif "brightness" in state.attributes:
            _LOGGER.debug(f"  → light has brightness, returning 'slider'")
            return "slider"
        # On/off only → use button
        else:
            _LOGGER.debug(f"  → light has no brightness/color_temp, returning 'button'")
            return "button"
    
    # Special handling for media_player - check for source list
    elif domain == "media_player":
        source_list = state.attributes.get("source_list")
        if source_list:
            _LOGGER.debug(f"  → media_player has source_list, returning 'select'")
            return "select"  # Has source selector
        else:
            _LOGGER.debug(f"  → media_player has no source_list, returning 'volume'")
            return "volume"  # Volume control only
    
    # Default mapping for other domains
    tile_type = DOMAIN_TO_TILE_TYPE.get(domain)
    if tile_type:
        _LOGGER.debug(f"  → {domain} mapped to {tile_type}")
        return tile_type
    
    _LOGGER.warning(f"No tile type mapping for domain: {domain}")
    return None
