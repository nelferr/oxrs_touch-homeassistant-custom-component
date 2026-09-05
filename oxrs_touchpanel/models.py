"""Models for the OXRS Touch Panel integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, Context
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers import config_validation as cv

from .const import CONF_ACTION_SEQUENCE, CONF_ACTION_MODE

_LOGGER = logging.getLogger(__name__)


class OxrsTileAction:
    """Represents a flexible action sequence for an OXRS tile.
    
    An action can contain:
    - Multiple service calls
    - Delays
    - Templates (with access to event data)
    - Conditions (if/then logic)
    - Error handling
    
    This uses Home Assistant's Script system for validation and execution.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        tile_id: str | None = None,
        action_index: int | None = None,
    ):
        """Initialize a tile action.
        
        Args:
            hass: Home Assistant instance
            config: Action configuration dict with 'sequence' and optional 'mode'
            tile_id: Optional tile identifier for logging
            action_index: Optional action index within tile for logging
        """
        self.hass = hass
        self.tile_id = tile_id
        self.action_index = action_index
        self.mode = config.get(CONF_ACTION_MODE, "single")
        self.sequence = config.get(CONF_ACTION_SEQUENCE, [])
        self.script: Script | None = None
        self.active = bool(self.sequence)

        # Validate and initialize script asynchronously
        if self.active:
            asyncio.create_task(self._init_script())

    async def _init_script(self) -> None:
        """Initialize the Script object with validated sequence."""
        if not self.sequence:
            self.active = False
            return

        try:
            # Validate the sequence using Home Assistant's schema
            validated_sequence = await async_validate_actions_config(
                self.hass, cv.SCRIPT_SCHEMA(self.sequence)
            )

            # Create the Script object
            script_name = f"oxrs_touchpanel_action"
            if self.tile_id:
                script_name += f"_{self.tile_id}"
            if self.action_index is not None:
                script_name += f"_{self.action_index}"

            self.script = Script(
                hass=self.hass,
                sequence=validated_sequence,
                name=script_name,
                domain="oxrs_touchpanel",
                logger=_LOGGER,
                script_mode=self.mode,
            )
            _LOGGER.debug(f"Initialized action: {script_name}")

        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                f"Failed to initialize action {self.tile_id}/{self.action_index}: {err}"
            )
            self.active = False

    async def run(
        self, data: dict[str, Any] | None = None, context: Context | None = None
    ) -> None:
        """Execute the action sequence.
        
        Args:
            data: Variables to pass to the script (will be accessible in templates)
            context: Home Assistant context for the execution
        """
        if not self.script:
            _LOGGER.debug(
                f"No script available for action {self.tile_id}/{self.action_index}"
            )
            return

        _LOGGER.debug(
            f"Running action sequence: {self.tile_id}/{self.action_index}"
        )
        self.hass.async_create_task(
            self.script.async_run(run_variables=data or {}, context=context)
        )

    def as_dict(self) -> dict[str, Any]:
        """Convert action to dictionary for serialization."""
        return {
            CONF_ACTION_MODE: self.mode,
            CONF_ACTION_SEQUENCE: self.sequence,
        }
