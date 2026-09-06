"""Config and options flows for the OXRS Touch Panel integration."""

from __future__ import annotations

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

from .const import (
    BUILTIN_ICONS,
    CONF_ACTION_MODE,
    CONF_ACTION_SEQUENCE,
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
from .migrations import migrate_tile_to_actions


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
        if user_input is not None:
            self._use_flexible_actions = user_input.get("tile_format") == "flexible"
            self._new_screen = int(user_input[CONF_SCREEN])
            
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
        return self.async_show_form(
            step_id="add_tile",
            data_schema=schema,
            description_placeholders={
                "help": "Choose 'Hardcoded' for simple entity control, or 'Flexible' for multi-step automations."
            },
        )

    async def async_step_add_tile_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1b: choose the tile type (old format)."""
        if user_input is not None:
            self._new_type = user_input[CONF_TYPE]
            return await self.async_step_add_tile_details()

        type_options = [
            {"value": key, "label": defn["label"]}
            for key, defn in TILE_TYPES.items()
        ]
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
        return self.async_show_form(
            step_id="add_tile_type",
            data_schema=schema,
            description_placeholders={"screen": str(self._new_screen)},
        )

    async def async_step_add_tile_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: pick a FREE position, the entity, label and icon."""
        assert self._new_type is not None
        definition = TILE_TYPES[self._new_type]

        max_positions = DEFAULT_LAYOUT["horizontal"] * DEFAULT_LAYOUT["vertical"]
        used = {
            t[CONF_TILE] for t in self._tiles if t[CONF_SCREEN] == self._new_screen
        }
        free = [i for i in range(1, max_positions + 1) if i not in used]
        if not free:
            return self.async_abort(reason="screen_full")

        if user_input is not None:
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
        return self.async_show_form(
            step_id="add_tile_details",
            data_schema=schema,
            description_placeholders={
                "type": definition["label"],
                "screen": str(self._new_screen),
            },
        )

    async def async_step_add_tile_actions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure flexible actions for a new tile."""
        max_positions = DEFAULT_LAYOUT["horizontal"] * DEFAULT_LAYOUT["vertical"]
        used = {
            t[CONF_TILE] for t in self._tiles if t[CONF_SCREEN] == self._new_screen
        }
        free = [i for i in range(1, max_positions + 1) if i not in used]
        
        if not free:
            return self.async_abort(reason="screen_full")

        if user_input is not None:
            # Build the new tile with flexible actions
            tile_config = {
                CONF_SCREEN: self._new_screen,
                CONF_TILE: int(user_input[CONF_TILE]),
                CONF_LABEL: user_input.get(CONF_LABEL, ""),
                CONF_ICON: user_input.get(CONF_ICON, ""),
                CONF_ACTIONS: [
                    {
                        CONF_ACTION_MODE: "single",
                        CONF_ACTION_SEQUENCE: user_input.get(
                            CONF_ACTION_SEQUENCE, []
                        ),
                    }
                ],
            }
            
            self._tiles.append(tile_config)
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
                vol.Optional(CONF_LABEL, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=False)
                ),
                vol.Optional(CONF_ICON, default=""): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=BUILTIN_ICONS,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_ACTION_SEQUENCE, default=[]): selector.TextSelector(
                    selector.TextSelectorConfig(
                        multiline=True,
                        mode="yaml",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="add_tile_actions",
            data_schema=schema,
            description_placeholders={
                "screen": str(self._new_screen),
                "example": (
                    "- service: light.turn_on\n"
                    "  data:\n"
                    "    entity_id: light.bedroom\n"
                    "    brightness_pct: 100\n"
                    "- delay:\n"
                    "    milliseconds: 500\n"
                    "- service: scene.turn_on\n"
                    "  data:\n"
                    "    entity_id: scene.movie_mode"
                ),
            },
        )

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
