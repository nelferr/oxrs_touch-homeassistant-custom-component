"""Find compatible OXRS tile types for entity domains.

This module queries the TILE_TYPES registry to find which tile styles
support a given entity domain. User then chooses from the compatible options.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def get_compatible_tile_types(
    tile_types: dict[str, Any], entity_domain: str, entity_state: Any = None
) -> list[tuple[str, dict[str, Any]]]:
    """Get all tile types compatible with an entity domain.
    
    Query TILE_TYPES and filter to only those that support this domain.
    For lights, optionally check entity_state to detect RGBW vs CCT vs brightness-only.
    
    Args:
        tile_types: TILE_TYPES dictionary from tiles.py
        entity_domain: Entity domain (e.g., "light", "cover")
        entity_state: Optional HA entity state (for lights, to detect RGBW/CCT capabilities)
        
    Returns:
        List of tuples (tile_type_name, tile_definition)
        Example for RGBW light: [("rgbw", {...}), ("cct", {...}), ("slider", {...}), ("button", {...})]
    """
    compatible = []
    
    for tile_type_name, tile_def in tile_types.items():
        tile_domain = tile_def.get("domain")
        
        # Normalize to list for comparison
        if isinstance(tile_domain, str):
            tile_domain = [tile_domain]
        elif not isinstance(tile_domain, list):
            tile_domain = []
        
        # If this tile type supports this entity domain, add it
        if entity_domain in tile_domain:
            compatible.append((tile_type_name, tile_def))
    
    # For lights, reorder by capability: RGBW > CCT > Slider > Button
    if entity_domain == "light" and entity_state and len(compatible) > 1:
        compatible = _sort_light_tile_types(compatible, entity_state)
    
    type_names = [name for name, _ in compatible]
    _LOGGER.debug(f"Compatible tile types for domain '{entity_domain}': {type_names}")
    return compatible


def _sort_light_tile_types(
    compatible: list[tuple[str, dict[str, Any]]], entity_state: Any
) -> list[tuple[str, dict[str, Any]]]:
    """Sort light tile types by capability.
    
    Priority:
    1. RGBW (if light has rgbw in supported_color_modes or type='rgbw')
    2. CCT (if light has color_temp_kelvin)
    3. Slider (if light has brightness)
    4. Button (fallback)
    """
    if entity_state is None:
        return compatible
    
    attributes = entity_state.attributes or {}
    supported_color_modes = attributes.get("supported_color_modes", [])
    light_type = attributes.get("type")
    
    # Check for RGBW support
    has_rgbw = (
        "rgbw" in supported_color_modes or
        light_type == "rgbw" or
        "rgbw_color" in attributes
    )
    
    # Check for CCT support
    has_cct = (
        "color_temp_kelvin" in attributes or
        "color_temp" in attributes or
        "color_temp" in supported_color_modes
    )
    
    # Check for brightness support
    has_brightness = "brightness" in attributes
    
    # Sort: remove items and re-add in priority order
    type_dict = {name: tile_def for name, tile_def in compatible}
    sorted_list = []
    
    # Priority 1: RGBW
    if has_rgbw and "rgbw" in type_dict:
        sorted_list.append(("rgbw", type_dict.pop("rgbw")))
    
    # Priority 2: CCT
    if has_cct and "cct" in type_dict:
        sorted_list.append(("cct", type_dict.pop("cct")))
    
    # Priority 3: Slider
    if has_brightness and "slider" in type_dict:
        sorted_list.append(("slider", type_dict.pop("slider")))
    
    # Priority 4: Add any remaining types (button, etc.) at the end
    sorted_list.extend(type_dict.items())
    
    _LOGGER.debug(
        f"Sorted light tile types - RGBW: {has_rgbw}, CCT: {has_cct}, Brightness: {has_brightness} → {[name for name, _ in sorted_list]}"
    )
    return sorted_list
