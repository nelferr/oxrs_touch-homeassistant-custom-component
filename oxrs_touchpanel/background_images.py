"""Manage background images for OXRS tiles.

This module handles:
- Storing background images in Home Assistant config
- Loading and validating images (JPG, PNG, GIF)
- Applying backgrounds to tile configurations via MQTT
- Managing image lifecycle (add, delete, list, apply)

OXRS Implementation Notes:
- Background images sent via cmnd/<device-client-id> topic
- Images encoded as base64 in JSON payload
- Field: "backgroundImage" with base64 data
- All tiles support background images
- Can combine with level display, text, and colors for rich UX
"""

from __future__ import annotations

import base64
import hashlib
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
CONFIG_IMAGE_FORMAT = "image_format"  # jpg, png, gif
CONFIG_IMAGE_SIZE = "image_size"  # File size in bytes

# Supported image formats
SUPPORTED_FORMATS = {"jpg", "jpeg", "png", "gif"}
MAX_IMAGE_SIZE = 4096  # OXRS firmware crashes if image > ~4KB


class BackgroundImageManager:
    """Manage background images for OXRS tiles.
    
    Handles:
    - Reading image files from disk (JPG, PNG, GIF)
    - Validating image format and size
    - Encoding images to base64 for storage
    - Storing in Home Assistant config entry
    - Building OXRS MQTT payloads for tiles
    - Listing and deleting images
    """

    def __init__(self, hass: HomeAssistant, config_entry_data: dict[str, Any]):
        """Initialize background image manager.
        
        Args:
            hass: Home Assistant instance
            config_entry_data: Config entry data containing stored images
        """
        self.hass = hass
        self.config_entry_data = config_entry_data
        self._images: dict[str, dict[str, Any]] = config_entry_data.get(BACKGROUND_IMAGES_KEY, {})

    async def add_image_from_file(
        self, file_path: str, image_name: str
    ) -> tuple[bool, str]:
        """Add a background image from a file path.
        
        Args:
            file_path: Path to image file (JPG, PNG, GIF)
            image_name: Display name for the image
            
        Returns:
            Tuple of (success, message)
        """
        try:
            # Read file
            path = Path(file_path)
            if not path.exists():
                return False, f"File not found: {file_path}"
            
            # Check file extension
            suffix = path.suffix.lower().lstrip(".")
            if suffix not in SUPPORTED_FORMATS:
                return False, f"Unsupported format: {suffix}. Supported: {', '.join(SUPPORTED_FORMATS)}"
            
            # Read and validate size
            image_data = path.read_bytes()
            file_size = len(image_data)
            
            if file_size == 0:
                return False, "File is empty"
            
            # OXRS firmware's 4KB limit applies to the base64-encoded string
            # sent over MQTT, not the raw file - base64 inflates size by ~33%,
            # so check the encoded length, matching config_flow.py's check.
            encoded_size = len(base64.b64encode(image_data))
            if encoded_size > MAX_IMAGE_SIZE:
                return False, (
                    f"Base64-encoded image too large: {encoded_size} chars "
                    f"(max: {MAX_IMAGE_SIZE}); raw file is {file_size} bytes"
                )
            
            # Generate image ID from file hash (deterministic, prevents duplicates)
            image_id = hashlib.md5(image_data).hexdigest()[:12]
            
            # Add the image
            success = await self.add_image(
                image_id, image_name, image_data, suffix
            )
            
            if success:
                return True, f"Image added: {image_name} ({file_size} bytes)"
            else:
                return False, "Failed to add image to storage"
                
        except Exception as err:
            _LOGGER.error(f"Error reading image file {file_path}: {err}", exc_info=True)
            return False, f"Error reading file: {str(err)}"

    async def add_image(
        self, image_id: str, image_name: str, image_data: bytes, image_format: str = "png"
    ) -> bool:
        """Add or update a background image.

        Stores base64-encoded image in config entry data.
        The caller (config_flow) is responsible for size/format validation.

        Args:
            image_id:     Stable MD5-based identifier
            image_name:   Display name (also used as OXRS addImage name)
            image_data:   Raw decoded image bytes
            image_format: File format: png, jpg or gif

        Returns:
            True if image was stored successfully
        """
        try:
            _LOGGER.debug(
                f"add_image: id={image_id!r} name={image_name!r} "
                f"format={image_format!r} size={len(image_data)} bytes"
            )

            # Re-encode to base64 for storage (we decoded it in config_flow to check size)
            b64_data = base64.b64encode(image_data).decode("utf-8")

            self._images[image_id] = {
                CONFIG_IMAGE_ID:     image_id,
                CONFIG_IMAGE_NAME:   image_name,
                CONFIG_IMAGE_DATA:   b64_data,
                CONFIG_IMAGE_FORMAT: image_format.lower(),
                CONFIG_IMAGE_SIZE:   len(image_data),
            }

            _LOGGER.info(
                f"Background image stored: '{image_name}' "
                f"(id={image_id}, {len(image_data)} bytes, {image_format})"
            )
            return True

        except Exception as err:
            _LOGGER.error(
                f"Error storing background image '{image_name}': {err}", exc_info=True
            )
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
        
        Stores reference to image in tile config for later MQTT transmission.
        The actual base64 data is sent via OXRS cmnd/ payload when tile updates.
        
        Uses the "background_image_name" key, matching the convention used
        by config_flow.py and hub.py's _inject_background - the OXRS firmware
        references images by name, not by our internal image_id.
        
        Args:
            tile_config: Tile configuration dict
            image_id: Background image ID to apply
            
        Returns:
            True if image was applied
        """
        image = self.get_image(image_id)
        if not image:
            _LOGGER.warning(f"Background image not found: {image_id}")
            return False
        
        tile_config["background_image_name"] = image[CONFIG_IMAGE_NAME]
        _LOGGER.debug(f"Applied background image {image_id} to tile")
        return True

    def remove_from_tile(self, tile_config: dict[str, Any]) -> None:
        """Remove background image from tile.
        
        Args:
            tile_config: Tile configuration dict
        """
        if "background_image_name" in tile_config:
            del tile_config["background_image_name"]

    def build_oxrs_add_image_payload(self, image_id: str) -> dict[str, Any] | None:
        """Build OXRS MQTT payload to upload/store background image.
        
        OXRS Firmware Two-Step Process:
        1. Send addImage command with name and base64 data
        2. Reference image by name in tile configuration
        
        This method builds Step 1: Upload the image to panel memory.
        After successful upload, use image name to reference in tiles.
        
        Args:
            image_id: Background image ID to upload
            
        Returns:
            OXRS MQTT payload dict for addImage command, or None if image not found
            
        Example payload:
        {
            "addImage": {
                "name": "living_room_bg_abc123",
                "imageBase64": "iVBORw0KGgo..."
            }
        }
        
        Note: Image names are auto-generated from MD5 hash of file
        Images are NOT persistent and must be reloaded on panel restart
        """
        image = self.get_image(image_id)
        if not image:
            _LOGGER.warning(f"Cannot build addImage payload: image not found: {image_id}")
            return None
        
        try:
            b64_data = image.get(CONFIG_IMAGE_DATA, "")
            image_name = image.get(CONFIG_IMAGE_NAME, "")
            
            # Validate image name (cannot start with underscore in OXRS)
            if image_name.startswith("_"):
                _LOGGER.warning(f"Image name cannot start with underscore: {image_name}")
                image_name = image_name.lstrip("_")
            
            return {
                "addImage": {
                    "name": image_name,
                    "imageBase64": b64_data,
                }
            }
        except Exception as err:
            _LOGGER.error(f"Error building addImage payload for {image_id}: {err}")
            return None

    def build_oxrs_tile_reference_payload(
        self, screen: int, tile: int, image_name: str
    ) -> dict[str, Any]:
        """Build OXRS MQTT payload to apply background image to tile.
        
        OXRS Firmware Two-Step Process:
        1. addImage (handled by build_oxrs_add_image_payload)
        2. Reference image by name in tile configuration ← This method
        
        This method builds Step 2: Apply previously uploaded image to a tile.
        
        Args:
            screen: Screen number (1-based)
            tile: Tile number (1-based)
            image_name: Previously registered image name
            
        Returns:
            OXRS MQTT payload dict for tiles command
            
        Example payload:
        {
            "tiles": [
                {
                    "screen": 1,
                    "tile": 1,
                    "backgroundImage": {
                        "name": "living_room_bg_abc123"
                    }
                }
            ]
        }
        """
        return {
            "tiles": [
                {
                    "screen": screen,
                    "tile": tile,
                    "backgroundImage": {
                        "name": image_name,
                    }
                }
            ]
        }
