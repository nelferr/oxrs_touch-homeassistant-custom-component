"""Config and options flows for the OXRS Touch Panel integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

_LOGGER = logging.getLogger(__name__)

from .const import (
    BUILTIN_ICONS,
    CONF_ACTION_ENTITY,
    CONF_ACTION_MODE,
    CONF_ACTION_SEQUENCE,
    CONF_ACTION_TILE_TYPE,
    CONF_ACTIONS,
    CONF_CLIENT_ID,
    CONF_ENTITY_ID,
    CONF_ICON,
    CONF_LABEL,
    CONF_NAME,
    CONF_SCREEN,
    CONF_TILE,
    CONF_TILES,
    CONF_TYPE,
    DEFAULT_LAYOUT,
    DOMAIN,
)
from .tiles import TILE_TYPES
from .domain_mapper import get_best_tile_type_for_entity


def _client_id_from_topic(topic: str) -> str | None:
    """Extract the client id from a ``stat/<client-id>/adopt`` topic."""
    parts = topic.split("/")
    if len(parts) >= 3 and parts[0] == "stat":
        return parts[1]
    return None


class OxrsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual setup of a panel."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._client_id: str | None = None

    async def async_step_mqtt(
        self, discovery_info: MqttServiceInfo
    ) -> ConfigFlowResult:
        """Handle a panel discovered via its MQTT adopt message."""
        client_id = _client_id_from_topic(discovery_info.topic)
        if client_id is None:
            return self.async_abort(reason="invalid_discovery")
        # Only adopt OXRS Touch Panels (the adopt payload carries the fw name).
        if "touchpanel" not in str(discovery_info.payload).lower():
            return self.async_abort(reason="not_oxrs_touchpanel")

        await self.async_set_unique_id(client_id)
        self._abort_if_unique_id_configured()

        self._client_id = client_id
        self.context["title_placeholders"] = {"name": client_id}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered panel."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._client_id,
                data={CONF_CLIENT_ID: self._client_id},
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._client_id},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup by entering the panel's MQTT client id."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client_id = user_input[CONF_CLIENT_ID].strip()
            await self.async_set_unique_id(client_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get(CONF_NAME) or client_id,
                data={CONF_CLIENT_ID: client_id},
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): str,
                vol.Optional(CONF_NAME): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return OxrsOptionsFlow(config_entry)


class OxrsOptionsFlow(OptionsFlow):
    """Add, remove and configure tiles from the UI."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialise the options flow."""
        self._entry = config_entry
        self._tiles: list[dict[str, Any]] = list(
            config_entry.options.get(CONF_TILES, [])
        )
        self._new_type: str | None = None
        self._new_screen: int = 1
        self._use_flexible_actions: bool = False
        self._action_sequence: list[dict[str, Any]] | None = None
        self._pending_tile: dict[str, Any] = {}  # Temporary storage for multi-step tile creation
        self._pending_tile_types: list[str] = []  # Available tile types for pending entity

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the tile-management menu."""
        return self.async_show_menu(
            step_id="init", menu_options=["add_tile", "remove_tile"]
        )

    async def async_step_add_tile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: choose tile format and target screen."""
        try:
            _LOGGER.debug(f"async_step_add_tile called with input: {user_input}")
            
            if user_input is not None:
                self._use_flexible_actions = user_input.get("tile_format") == "flexible"
                self._new_screen = int(user_input[CONF_SCREEN])
                
                _LOGGER.debug(f"User chose format: {'flexible' if self._use_flexible_actions else 'hardcoded'}, screen: {self._new_screen}")
                
                if self._use_flexible_actions:
                    # New format: go to flexible actions configuration
                    return await self.async_step_add_tile_actions()
                else:
                    # Old format: choose tile type
                    return await self.async_step_add_tile_type()

            schema = vol.Schema(
                {
                    vol.Required("tile_format", default="hardcoded"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": "hardcoded", "label": "Hardcoded tile type (light, switch, climate, etc.)"},
                                {"value": "flexible", "label": "Flexible actions (advanced: service sequences, templates, etc.)"},
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_SCREEN, default=1): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1, max=32, mode="box")
                    ),
                }
            )
            
            _LOGGER.debug("Showing initial tile format selection form")
            return self.async_show_form(
                step_id="add_tile",
                data_schema=schema,
                description_placeholders={
                    "help": "Choose 'Hardcoded' for simple entity control, or 'Flexible' for multi-step automations."
                },
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_tile: {err}", exc_info=True)
            return self.async_abort(reason="invalid_format")

    async def async_step_add_tile_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1b: choose the tile type (old format)."""
        try:
            _LOGGER.debug(f"async_step_add_tile_type called with input: {user_input}")
            
            if user_input is not None:
                self._new_type = user_input[CONF_TYPE]
                _LOGGER.debug(f"User chose tile type: {self._new_type}")
                return await self.async_step_add_tile_details()

            type_options = [
                {"value": key, "label": defn["label"]}
                for key, defn in TILE_TYPES.items()
            ]
            
            _LOGGER.debug(f"Available tile types: {[opt['value'] for opt in type_options]}")
            
            schema = vol.Schema(
                {
                    vol.Required(CONF_TYPE, default=next(iter(TILE_TYPES))): (
                        selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=type_options,
                                mode=selector.SelectSelectorMode.DROPDOWN,
                            )
                        )
                    ),
                }
            )
            
            _LOGGER.debug("Showing tile type selection form")
            return self.async_show_form(
                step_id="add_tile_type",
                data_schema=schema,
                description_placeholders={"screen": str(self._new_screen)},
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_tile_type: {err}", exc_info=True)
            return self.async_abort(reason="invalid_type")

    async def async_step_add_tile_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: pick a FREE position, the entity, label and icon."""
        try:
            _LOGGER.debug(f"async_step_add_tile_details called with input: {list(user_input.keys()) if user_input else 'None'}")
            
            assert self._new_type is not None
            definition = TILE_TYPES[self._new_type]
            _LOGGER.debug(f"Tile type definition: {self._new_type}")

            max_positions = DEFAULT_LAYOUT["horizontal"] * DEFAULT_LAYOUT["vertical"]
            used = {
                t[CONF_TILE] for t in self._tiles if t[CONF_SCREEN] == self._new_screen
            }
            free = [i for i in range(1, max_positions + 1) if i not in used]
            
            _LOGGER.debug(f"Free positions: {free}")
            
            if not free:
                _LOGGER.warning(f"Screen {self._new_screen} is full")
                return self.async_abort(reason="screen_full")

            if user_input is not None:
                _LOGGER.debug(f"Creating hardcoded tile with entity: {user_input[CONF_ENTITY_ID]}")
                self._tiles.append(
                    {
                        CONF_SCREEN: self._new_screen,
                        CONF_TILE: int(user_input[CONF_TILE]),
                        CONF_TYPE: self._new_type,
                        CONF_ENTITY_ID: user_input[CONF_ENTITY_ID],
                        CONF_LABEL: user_input.get(CONF_LABEL, ""),
                        CONF_ICON: user_input.get(CONF_ICON, definition["icon"]),
                    }
                )
                _LOGGER.info(f"Hardcoded tile created at {self._new_screen}/{user_input[CONF_TILE]}")
                return self.async_create_entry(title="", data={CONF_TILES: self._tiles})

            tile_options = [
                {"value": str(i), "label": f"Position {i}"}
                for i in free
            ]
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_TILE, default=str(free[0])
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=tile_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=definition["domain"])
                    ),
                    vol.Optional(CONF_LABEL, default=""): selector.TextSelector(),
                    vol.Optional(
                        CONF_ICON, default=definition["icon"]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=BUILTIN_ICONS,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )
            
            _LOGGER.debug("Showing tile details form")
            return self.async_show_form(
                step_id="add_tile_details",
                data_schema=schema,
                description_placeholders={
                    "type": definition["label"],
                    "screen": str(self._new_screen),
                },
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_tile_details: {err}", exc_info=True)
            return self.async_abort(reason="invalid_details")

    async def async_step_add_tile_actions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure flexible actions for a new tile."""
        try:
            _LOGGER.debug(f"async_step_add_tile_actions called with input: {user_input}")
            
            max_positions = DEFAULT_LAYOUT["horizontal"] * DEFAULT_LAYOUT["vertical"]
            used = {
                t[CONF_TILE] for t in self._tiles if t[CONF_SCREEN] == self._new_screen
            }
            free = [i for i in range(1, max_positions + 1) if i not in used]
            
            _LOGGER.debug(f"Screen {self._new_screen} has positions: {free}")
            
            if not free:
                _LOGGER.error(f"Screen {self._new_screen} is full")
                return self.async_abort(reason="screen_full")

            if user_input is not None:
                _LOGGER.debug(f"Processing user input: {list(user_input.keys())}")
                
                # ActionSelector returns a list of actions directly
                sequence = user_input.get(CONF_ACTION_SEQUENCE, [])
                _LOGGER.debug(f"Sequence type: {type(sequence)}, value: {sequence}")
                
                # Build the new flexible tile
                action_entity = user_input.get(CONF_ACTION_ENTITY, "").strip() or None
                
                _LOGGER.debug(f"Building flexible tile - entity: {action_entity}")
                
                # Store the tile details for the next step
                self._pending_tile = {
                    CONF_SCREEN: self._new_screen,
                    CONF_TILE: int(user_input[CONF_TILE]),
                    CONF_LABEL: user_input.get(CONF_LABEL, ""),
                    CONF_ICON: user_input.get(CONF_ICON, ""),
                }
                
                # Verify entity exists and get available tile types
                if action_entity:
                    entity_state = self.hass.states.get(action_entity)
                    if entity_state is None:
                        _LOGGER.warning(f"Entity not found: {action_entity}")
                        return self.async_show_form(
                            step_id="add_tile_actions",
                            data_schema=self._build_add_tile_actions_schema(free),
                            errors={"base": "entity_not_found"},
                            description_placeholders={
                                "screen": str(self._new_screen),
                            },
                        )
                    
                    # Get ALL available tile types for this entity
                    from .domain_mapper import get_all_tile_types_for_entity
                    available_types = get_all_tile_types_for_entity(self.hass, action_entity)
                    
                    if not available_types:
                        _LOGGER.warning(f"Unsupported entity domain for {action_entity}")
                        return self.async_show_form(
                            step_id="add_tile_actions",
                            data_schema=self._build_add_tile_actions_schema(free),
                            errors={"base": "unsupported_entity"},
                            description_placeholders={
                                "screen": str(self._new_screen),
                            },
                        )
                    
                    # Store entity info and available types
                    self._pending_tile[CONF_ACTION_ENTITY] = action_entity
                    self._pending_tile_types = available_types
                    
                    _LOGGER.info(f"Entity {action_entity} has {len(available_types)} tile type options: {available_types}")
                    
                    # Go to tile style selection step
                    return await self.async_step_add_tile_style()
                else:
                    _LOGGER.warning("Flexible tile created without entity binding")
                    # Create entry without entity binding (unsupported, but let it through for now)
                    self._pending_tile[CONF_ACTIONS] = [{}]
                    self._tiles.append(self._pending_tile)
                    return self.async_create_entry(title="", data={CONF_TILES: self._tiles})

            _LOGGER.debug(f"Showing form for screen {self._new_screen}")
            return self.async_show_form(
                step_id="add_tile_actions",
                data_schema=self._build_add_tile_actions_schema(free),
                description_placeholders={
                    "screen": str(self._new_screen),
                },
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_tile_actions: {err}", exc_info=True)
            return self.async_abort(reason="invalid_actions")

    async def async_step_add_tile_style(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose which tile style for the flexible tile."""
        try:
            if user_input is not None:
                _LOGGER.debug(f"async_step_add_tile_style called with input: {user_input}")
                
                chosen_type = user_input.get("tile_style")
                _LOGGER.info(f"User chose tile style: {chosen_type}")
                
                # Build final tile config with entity AND chosen tile type
                tile_config = {
                    **self._pending_tile,
                    CONF_ACTION_TILE_TYPE: chosen_type,  # Store the chosen style
                    CONF_ACTIONS: [{}],  # Mark as flexible tile
                }
                
                _LOGGER.info(f"Final flexible tile config: {tile_config}")
                self._tiles.append(tile_config)
                
                return self.async_create_entry(title="", data={CONF_TILES: self._tiles})
            
            # Show form to choose tile style
            if not hasattr(self, "_pending_tile_types"):
                return self.async_abort(reason="invalid_actions")
            
            # Create select options for available tile types
            type_options = [
                {"value": t, "label": self._format_tile_type_name(t)}
                for t in self._pending_tile_types
            ]
            
            _LOGGER.debug(f"Showing tile style selector with options: {type_options}")
            
            schema = vol.Schema({
                vol.Required("tile_style"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=type_options,
                        mode="dropdown",
                    )
                )
            })
            
            return self.async_show_form(
                step_id="add_tile_style",
                data_schema=schema,
                description_placeholders={
                    "entity": self._pending_tile.get(CONF_ACTION_ENTITY, ""),
                },
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_tile_style: {err}", exc_info=True)
            return self.async_abort(reason="invalid_actions")

    @staticmethod
    def _format_tile_type_name(tile_type: str) -> str:
        """Format tile type name for display."""
        names = {
            "cct": "Color & Brightness",
            "slider": "Brightness Only",
            "updown": "Open/Close/Stop",
            "thermostat": "Thermostat",
            "button": "On/Off Toggle",
            "volume": "Volume Control",
            "select": "Selector",
        }
        return names.get(tile_type, tile_type)
    
    def _build_add_tile_actions_schema(self, free: list[int]) -> vol.Schema:
        """Build the schema for adding tile actions with proper action builder."""
        try:
            _LOGGER.debug(f"Building schema for free positions: {free}")
            
            tile_options = [
                {"value": str(i), "label": f"Position {i}"}
                for i in free
            ]

            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_TILE, default=str(free[0])
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=tile_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(CONF_LABEL, default=""): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=False)
                    ),
                    vol.Optional(CONF_ICON, default=""): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=BUILTIN_ICONS,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(CONF_ACTION_ENTITY, default=""): selector.EntitySelector(
                        selector.EntitySelectorConfig()
                    ),
                    vol.Required(CONF_ACTION_MODE, default="single"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": "single", "label": "Single (one at a time)"},
                                {"value": "parallel", "label": "Parallel (all at once)"},
                                {"value": "queued", "label": "Queued (wait for each)"},
                                {"value": "restart", "label": "Restart (restart if triggered again)"},
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_ACTION_SEQUENCE, default=[]): selector.ActionSelector(),
                }
            )
            
            _LOGGER.debug("Schema built successfully")
            return schema
            
        except Exception as err:
            _LOGGER.error(f"Error building schema: {err}", exc_info=True)
            raise

    async def async_step_remove_tile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove an existing tile."""
        if not self._tiles:
            return self.async_abort(reason="no_tiles")
        if user_input is not None:
            del self._tiles[int(user_input["index"])]
            return self.async_create_entry(title="", data={CONF_TILES: self._tiles})

        options = [
            {
                "value": str(index),
                "label": (
                    f"S{tile[CONF_SCREEN]}·T{tile[CONF_TILE]} "
                    f"[{tile.get(CONF_TYPE, '')}] "
                    f"{tile.get(CONF_LABEL) or ''} ({tile.get(CONF_ENTITY_ID, 'N/A')})"
                ),
            }
            for index, tile in enumerate(self._tiles)
        ]
        schema = vol.Schema(
            {
                vol.Required("index"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_tile", data_schema=schema)
