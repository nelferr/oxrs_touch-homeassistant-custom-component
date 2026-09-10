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
        self._new_tile_config: dict[str, Any] | None = None
        
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the tile-management menu."""
        return self.async_show_menu(
            step_id="init", menu_options=["add_tile", "remove_tile", "manage_background_images"]
        )

    async def async_step_add_tile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Choose target screen, then tile type."""
        try:
            _LOGGER.debug(f"async_step_add_tile called with input: {user_input}")
            
            if user_input is not None:
                self._new_screen = int(user_input[CONF_SCREEN])
                _LOGGER.debug(f"User chose screen: {self._new_screen}")
                
                # Go to tile type selection
                return await self.async_step_add_tile_type()

            schema = vol.Schema(
                {
                    vol.Required(CONF_SCREEN, default=1): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1, max=32, mode="box")
                    ),
                }
            )
            
            _LOGGER.debug("Showing screen selection form")
            return self.async_show_form(
                step_id="add_tile",
                data_schema=schema,
                description_placeholders={
                    "help": "Choose which screen to add the tile to."
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

    def _get_entity_filter_for_tile_type(self, tile_type: str):
        """Build entity filter based on tile type and its requirements."""
        if tile_type == "rgbw":
            # RGBW: light must have BOTH "hs" and "rgbw" in supported_color_modes
            def filter_rgbw(entity):
                if entity.domain != "light":
                    return False
                state = self.hass.states.get(entity.entity_id)
                if not state:
                    return False
                color_modes = state.attributes.get("supported_color_modes", [])
                return "hs" in color_modes and "rgbw" in color_modes
            return filter_rgbw
        
        elif tile_type == "cct":
            # CCT: light must have color_temp capability
            def filter_cct(entity):
                if entity.domain != "light":
                    return False
                state = self.hass.states.get(entity.entity_id)
                if not state:
                    return False
                # Check for color_temp_kelvin, color_temp, or color_temp in modes
                color_modes = state.attributes.get("supported_color_modes", [])
                has_temp = (
                    "color_temp_kelvin" in state.attributes or
                    "color_temp" in state.attributes or
                    "color_temp" in color_modes
                )
                return has_temp
            return filter_cct
        
        elif tile_type == "slider":
            # Slider: light must have brightness
            def filter_slider(entity):
                if entity.domain != "light":
                    return False
                state = self.hass.states.get(entity.entity_id)
                if not state:
                    return False
                return "brightness" in state.attributes
            return filter_slider
        
        elif tile_type == "updown":
            # UpDown: cover entity
            return lambda entity: entity.domain == "cover"
        
        elif tile_type == "thermostat":
            # Thermostat: climate entity
            return lambda entity: entity.domain == "climate"
        
        elif tile_type == "volume":
            # Volume: media_player entity
            return lambda entity: entity.domain == "media_player"
        
        elif tile_type == "select":
            # Select: select or input_select entity
            return lambda entity: entity.domain in ("select", "input_select")
        
        elif tile_type == "button":
            # Button: switch, scene, script, button, input_button
            return lambda entity: entity.domain in ("switch", "scene", "script", "button", "input_button")
        
        return None

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
                # Store tile details for next step (background image selection)
                self._new_tile_config = {
                    CONF_SCREEN: self._new_screen,
                    CONF_TILE: int(user_input[CONF_TILE]),
                    CONF_TYPE: self._new_type,
                    CONF_ENTITY_ID: user_input[CONF_ENTITY_ID],
                    CONF_LABEL: user_input.get(CONF_LABEL, ""),
                    CONF_ICON: user_input.get(CONF_ICON, definition["icon"]),
                }
                # Go to background image selection step
                return await self.async_step_add_tile_background()

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

    async def async_step_add_tile_background(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: optionally select a background image for the tile."""
        try:
            _LOGGER.debug(f"async_step_add_tile_background called with input: {list(user_input.keys()) if user_input else 'None'}")
            
            assert self._new_tile_config is not None
            
            if user_input is not None:
                # Add tile to list
                self._tiles.append(self._new_tile_config)
                
                # Optionally add background image (store image_name, not id)
                background_image_name = user_input.get("background_image_name")
                if background_image_name and background_image_name != "none":
                    self._tiles[-1]["background_image_name"] = background_image_name
                    _LOGGER.info(f"Added background image '{background_image_name}' to tile")
                
                _LOGGER.info(f"Tile created at screen {self._new_screen}/position {self._new_tile_config[CONF_TILE]}")
                return self.async_create_entry(title="", data={CONF_TILES: self._tiles})
            
            # Get background image options from manager
            hub = self.hass.data.get(DOMAIN, {})
            background_images = []
            
            if hub:
                # Try to get images from this entry's panel manager
                entry_id = self._entry.entry_id if hasattr(self, "_entry") else None
                if entry_id:
                    panel = hub.get(entry_id)
                    if panel and hasattr(panel, "background_images"):
                        images = panel.background_images.list_images()
                        background_images = [
                            {"value": img["image_name"], "label": img["image_name"]}
                            for img in images
                        ]
            
            # Add "None" option to skip background image
            image_options = [{"value": "none", "label": "No background image"}]
            image_options.extend(background_images)
            
            schema = vol.Schema(
                {
                    vol.Optional("background_image_name", default="none"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=image_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )
            
            _LOGGER.debug("Showing background image selection form")
            return self.async_show_form(
                step_id="add_tile_background",
                data_schema=schema,
                description_placeholders={
                    "tile": f"Screen {self._new_screen}, Position {self._new_tile_config[CONF_TILE]}",
                    "images_available": f"{len(background_images)} image(s) available",
                },
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_tile_background: {err}", exc_info=True)
            return self.async_abort(reason="invalid_details")

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

    async def async_step_manage_background_images(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a background image — paste base64 from OXRS Asset Generator."""
        import re as _re, base64 as _b64, hashlib as _hl
        try:
            if user_input is not None:
                image_name   = user_input.get("image_name",   "").strip()
                image_base64 = user_input.get("image_base64", "").strip()

                if not image_name:
                    return self.async_show_form(
                        step_id="manage_background_images",
                        data_schema=self._build_background_images_schema(),
                        errors={"base": "no_name"},
                    )
                if not image_base64:
                    return self.async_show_form(
                        step_id="manage_background_images",
                        data_schema=self._build_background_images_schema(),
                        errors={"base": "no_file"},
                    )

                # Strip data URI prefix if user pasted from browser
                m = _re.match(r"data:image/[^;]+;base64,(.+)", image_base64, _re.DOTALL)
                if m:
                    image_base64 = m.group(1).strip()

                # Validate base64
                try:
                    image_bytes = _b64.b64decode(image_base64, validate=True)
                except Exception:
                    return self.async_show_form(
                        step_id="manage_background_images",
                        data_schema=self._build_background_images_schema(),
                        errors={"base": "image_error"},
                    )

                # OXRS firmware crashes on images > 4 KB
                if len(image_bytes) > 4096:
                    _LOGGER.warning(
                        f"Image too large: {len(image_bytes)} bytes (OXRS limit 4 KB)"
                    )
                    return self.async_show_form(
                        step_id="manage_background_images",
                        data_schema=self._build_background_images_schema(),
                        errors={"base": "image_too_large"},
                    )

                # OXRS forbids names starting with underscore
                if image_name.startswith("_"):
                    return self.async_show_form(
                        step_id="manage_background_images",
                        data_schema=self._build_background_images_schema(),
                        errors={"base": "invalid_image_name"},
                    )

                # Detect format from magic bytes
                if image_bytes[:4] == b"\x89PNG":
                    fmt = "png"
                elif image_bytes[:2] == b"\xff\xd8":
                    fmt = "jpg"
                elif image_bytes[:3] == b"GIF":
                    fmt = "gif"
                else:
                    fmt = "png"

                image_id = _hl.md5(image_base64.encode()).hexdigest()[:12]

                entry_id = self._entry.entry_id
                panel = self.hass.data.get(DOMAIN, {}).get(entry_id)
                if not panel or not hasattr(panel, "background_images"):
                    _LOGGER.error(f"Cannot access panel for entry {entry_id}")
                    return self.async_abort(reason="invalid_format")

                success = await panel.background_images.add_image(
                    image_id, image_name, image_bytes, fmt
                )
                if not success:
                    return self.async_show_form(
                        step_id="manage_background_images",
                        data_schema=self._build_background_images_schema(),
                        errors={"base": "image_error"},
                    )

                from .const import CONF_BACKGROUND_IMAGES
                new_options = dict(self._entry.options)
                new_options[CONF_BACKGROUND_IMAGES] = panel.background_images._images
                self.hass.config_entries.async_update_entry(
                    self._entry, options=new_options
                )
                _LOGGER.info(
                    f"Background image saved: '{image_name}' "
                    f"(id={image_id}, {len(image_bytes)} B, {fmt})"
                )
                return self.async_abort(reason="image_uploaded")

            return self.async_show_form(
                step_id="manage_background_images",
                data_schema=self._build_background_images_schema(),
            )
        except Exception as err:
            _LOGGER.error(
                f"Error in async_step_manage_background_images: {err}", exc_info=True
            )
            return self.async_abort(reason="invalid_format")

    def _build_background_images_schema(self) -> vol.Schema:
        """Build schema for background image management — paste base64 string."""
        return vol.Schema(
            {
                vol.Required("image_name"): selector.TextSelector(),
                vol.Required("image_base64"): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )
