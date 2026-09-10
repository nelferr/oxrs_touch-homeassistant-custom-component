"""Manage background images for OXRS tiles.

This module handles:
- Storing background images in Home Assistant config
- Loading and validating images
- Applying backgrounds to tile configurations
- Managing image lifecycle
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Background images storage key
BACKGROUND_IMAGES_KEY = "background_images"
CONFIG_IMAGE_ID = "image_id"
CONFIG_IMAGE_NAME = "image_name"
CONFIG_IMAGE_DATA = "image_data"  # Base64 encoded image
CONFIG_IMAGE_FORMAT = "image_format"  # jpg, png, etc


class BackgroundImageManager:
    """Manage background images for tiles."""

    def __init__(self, hass: HomeAssistant, config_entry_data: dict[str, Any]):
        """Initialize background image manager.
        
        Args:
            hass: Home Assistant instance
            config_entry_data: Config entry data containing stored images
        """
        self.hass = hass
        self.config_entry_data = config_entry_data
        self._images: dict[str, dict[str, Any]] = config_entry_data.get(BACKGROUND_IMAGES_KEY, {})

    async def add_image(
        self, image_id: str, image_name: str, image_data: bytes, image_format: str = "jpg"
    ) -> bool:
        """Add or update a background image.
        
        Args:
            image_id: Unique identifier for the image
            image_name: Display name for the image
            image_data: Raw image bytes
            image_format: Image format (jpg, png, gif, etc)
            
        Returns:
            True if image was added successfully
        """
        try:
            # Encode image to base64 for storage
            b64_data = base64.b64encode(image_data).decode("utf-8")
            
            self._images[image_id] = {
                CONFIG_IMAGE_ID: image_id,
                CONFIG_IMAGE_NAME: image_name,
                CONFIG_IMAGE_DATA: b64_data,
                CONFIG_IMAGE_FORMAT: image_format,
            }
            
            # Update config entry data
            self.config_entry_data[BACKGROUND_IMAGES_KEY] = self._images
            
            _LOGGER.info(f"Added background image: {image_id} ({image_name})")
            return True
        except Exception as err:
            _LOGGER.error(f"Error adding background image {image_id}: {err}")
            return False

    def get_image(self, image_id: str) -> dict[str, Any] | None:
        """Get background image by ID.
        
        Args:
            image_id: Image identifier
            
        Returns:
            Image dict with metadata and base64 data, or None if not found
        """
        return self._images.get(image_id)

    def get_image_data(self, image_id: str) -> bytes | None:
        """Get decoded image data.
        
        Args:
            image_id: Image identifier
            
        Returns:
            Raw image bytes, or None if not found
        """
        image = self.get_image(image_id)
        if not image:
            return None
        
        try:
            b64_data = image.get(CONFIG_IMAGE_DATA, "")
            return base64.b64decode(b64_data)
        except Exception as err:
            _LOGGER.error(f"Error decoding image {image_id}: {err}")
            return None

    def list_images(self) -> list[dict[str, Any]]:
        """List all available background images.
        
        Returns:
            List of image metadata (without base64 data for efficiency)
        """
        return [
            {
                CONFIG_IMAGE_ID: img[CONFIG_IMAGE_ID],
                CONFIG_IMAGE_NAME: img[CONFIG_IMAGE_NAME],
                CONFIG_IMAGE_FORMAT: img[CONFIG_IMAGE_FORMAT],
            }
            for img in self._images.values()
        ]

    def delete_image(self, image_id: str) -> bool:
        """Delete a background image.
        
        Args:
            image_id: Image identifier
            
        Returns:
            True if image was deleted
        """
        if image_id in self._images:
            del self._images[image_id]
            self.config_entry_data[BACKGROUND_IMAGES_KEY] = self._images
            _LOGGER.info(f"Deleted background image: {image_id}")
            return True
        return False

    def apply_to_tile(self, tile_config: dict[str, Any], image_id: str) -> bool:
        """Apply background image to tile configuration.
        
        Args:
            tile_config: Tile configuration dict
            image_id: Background image ID to apply
            
        Returns:
            True if image was applied
        """
        if not self.get_image(image_id):
            _LOGGER.warning(f"Background image not found: {image_id}")
            return False
        
        tile_config["background_image_id"] = image_id
        _LOGGER.debug(f"Applied background image {image_id} to tile")
        return True

    def remove_from_tile(self, tile_config: dict[str, Any]) -> None:
        """Remove background image from tile.
        
        Args:
            tile_config: Tile configuration dict
        """
        if "background_image_id" in tile_config:
            del tile_config["background_image_id"]
