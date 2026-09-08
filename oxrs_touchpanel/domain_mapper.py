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
    
    # Special handling for lights - check attributes
    if domain == "light":
        # Color temp support → use CCT tile
        if "color_temp_kelvin" in state.attributes or "color_temp" in state.attributes:
            return "cct"
        # Brightness support → use slider
        elif "brightness" in state.attributes:
            return "slider"
        # On/off only → use button
        else:
            return "button"
    
    # Special handling for media_player - check for source list
    elif domain == "media_player":
        source_list = state.attributes.get("source_list")
        if source_list:
            return "select"  # Has source selector
        else:
            return "volume"  # Volume control only
    
    # Default mapping for other domains
    tile_type = DOMAIN_TO_TILE_TYPE.get(domain)
    if tile_type:
        _LOGGER.debug(f"Mapped {entity_id} ({domain}) → {tile_type}")
        return tile_type
    
    _LOGGER.warning(f"No tile type mapping for domain: {domain}")
    return None
