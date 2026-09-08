"""Find compatible OXRS tile types for entity domains.

This module queries the TILE_TYPES registry to find which tile styles
support a given entity domain. User then chooses from the compatible options.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def get_compatible_tile_types(
    tile_types: dict[str, Any], entity_domain: str
) -> list[tuple[str, dict[str, Any]]]:
    """Get all tile types compatible with an entity domain.
    
    Query TILE_TYPES and filter to only those that support this domain.
    
    Args:
        tile_types: TILE_TYPES dictionary from tiles.py
        entity_domain: Entity domain (e.g., "light", "cover")
        
    Returns:
        List of tuples (tile_type_name, tile_definition)
        Example for light: [("cct", {...}), ("slider", {...}), ("button", {...})]
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
    
    type_names = [name for name, _ in compatible]
    _LOGGER.debug(f"Compatible tile types for domain '{entity_domain}': {type_names}")
    return compatible
