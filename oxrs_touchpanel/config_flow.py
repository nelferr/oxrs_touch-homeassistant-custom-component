"""Config and options flows for the OXRS Touch Panel integration."""

from __future__ import annotations

import base64
import hashlib
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
    CONF_INDICATOR_SECONDARY_ENTITY_ID,
    CONF_LABEL,
    CONF_NAME,
    CONF_SCREEN,
    CONF_SCREEN_NAMES,
    CONF_SUBLABEL_ENTITY_ID,
    CONF_TILE,
    CONF_TILES,
    CONF_TYPE,
    DEFAULT_LAYOUT,
    DOMAIN,
    LIBRARY_DATA_KEY,
)
from .library import ICON_CATEGORIES, SharedMediaLibrary
from .tiles import TILE_TYPES, eligible_entity_ids


def _client_id_from_topic(topic: str) -> str | None:
    """Extract the client id from a ``stat/<client-id>/adopt`` topic."""
    parts = topic.split("/")
    if len(parts) >= 3 and parts[0] == "stat":
        return parts[1]
    return None


def _decode_and_validate_base64_image(
    image_base64: str, *, require_png: bool = False
) -> tuple[bytes | None, str, str | None]:
    """Decode and validate a pasted base64 image string.

    Shared by background-image and custom-icon management, since both
    accept the same "paste from the OXRS Asset Generator" input and follow
    the same OXRS rules (4KB encoded limit; PNG required for icons).

    Returns (image_bytes, format, error_key). error_key is None on success;
    image_bytes/format are only meaningful when error_key is None.
    """
    import re

    image_base64 = image_base64.strip()

    # Strip a data URI prefix if the user pasted it straight from a browser
    m = re.match(r"data:image/[^;]+;base64,(.+)", image_base64, re.DOTALL)
    if m:
        image_base64 = m.group(1).strip()

    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception as err:
        _LOGGER.error(f"Base64 decode failed: {err}")
        return None, "", "image_error"

    # OXRS docs: "encoded image should not exceed 4KB" - the base64 string
    if len(image_base64) > 4096:
        _LOGGER.warning(f"Base64 string too large: {len(image_base64)} chars (OXRS limit ~4KB)")
        return None, "", "image_too_large"

    if image_bytes[:4] == bytes([0x89, 0x50, 0x4E, 0x47]):
        fmt = "png"
    elif image_bytes[:2] == bytes([0xFF, 0xD8]):
        fmt = "jpg"
    elif image_bytes[:3] == b"GIF":
        fmt = "gif"
    else:
        fmt = "png"

    if require_png and fmt != "png":
        return None, "", "icon_must_be_png"

    return image_bytes, fmt, None


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
        self._screen_names: dict[str, str] = dict(
            config_entry.options.get(CONF_SCREEN_NAMES, {})
        )
        self._new_type: str | None = None
        self._new_screen: int = 1
        self._new_tile_config: dict[str, Any] | None = None

    def _get_library(self) -> SharedMediaLibrary | None:
        """Return the shared media library, if the integration has finished
        loading it (it always will have, by the time an options flow for an
        already-configured panel can be opened)."""
        return self.hass.data.get(LIBRARY_DATA_KEY)

    def _build_icon_options(self) -> list[dict[str, str]]:
        """Combine firmware built-in icons with custom icons from the shared
        library into one selector option list. Custom icon labels are
        prefixed with their category so they group naturally when the
        dropdown is sorted/scanned, without relying on disabled separator
        rows (which don't render consistently across HA frontend versions)."""
        options = [{"value": name, "label": name} for name in BUILTIN_ICONS]
        library = self._get_library()
        if library:
            for icon in library.list_icons():
                category_label = ICON_CATEGORIES.get(icon.get("category", ""), "Other")
                options.append(
                    {"value": icon["name"], "label": f"{category_label}: {icon['name']}"}
                )
        return options

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the tile-management menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_tile",
                "remove_tile",
                "rename_screen",
                "manage_background_images",
                "manage_custom_icons",
            ],
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
                
                # Give the user a chance to (re)name this screen before
                # picking the tile type.
                return await self.async_step_name_screen()

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

    async def async_step_name_screen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally set/change the display name for the chosen screen."""
        try:
            current_name = self._screen_names.get(
                str(self._new_screen), f"Screen {self._new_screen}"
            )
            if user_input is not None:
                new_name = user_input.get("screen_name", "").strip()
                if new_name:
                    self._screen_names[str(self._new_screen)] = new_name
                    _LOGGER.debug(f"Screen {self._new_screen} named '{new_name}'")
                return await self.async_step_add_tile_type()

            schema = vol.Schema(
                {
                    vol.Optional("screen_name", default=current_name): selector.TextSelector(),
                }
            )
            return self.async_show_form(
                step_id="name_screen",
                data_schema=schema,
                description_placeholders={"screen": str(self._new_screen)},
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_name_screen: {err}", exc_info=True)
            return self.async_abort(reason="invalid_format")

    async def async_step_rename_screen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rename an existing screen without adding a tile."""
        try:
            known_screens = sorted({t[CONF_SCREEN] for t in self._tiles})
            if not known_screens:
                return self.async_abort(reason="no_screens")

            if user_input is not None:
                screen = int(user_input["screen"])
                new_name = user_input.get("screen_name", "").strip()
                if new_name:
                    self._screen_names[str(screen)] = new_name
                new_options = dict(self._entry.options)
                new_options[CONF_SCREEN_NAMES] = self._screen_names
                return self.async_create_entry(title="", data=new_options)

            screen_options = [
                {
                    "value": str(s),
                    "label": self._screen_names.get(str(s), f"Screen {s}"),
                }
                for s in known_screens
            ]
            schema = vol.Schema(
                {
                    vol.Required("screen", default=str(known_screens[0])): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=screen_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional("screen_name", default=""): selector.TextSelector(),
                }
            )
            return self.async_show_form(step_id="rename_screen", data_schema=schema)
        except Exception as err:
            _LOGGER.error(f"Error in async_step_rename_screen: {err}", exc_info=True)
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
                # Store tile details for next step (background image selection)
                self._new_tile_config = {
                    CONF_SCREEN: self._new_screen,
                    CONF_TILE: int(user_input[CONF_TILE]),
                    CONF_TYPE: self._new_type,
                    CONF_ENTITY_ID: user_input[CONF_ENTITY_ID],
                    CONF_LABEL: user_input.get(CONF_LABEL, ""),
                    CONF_ICON: user_input.get(CONF_ICON, definition["icon"]),
                }
                sublabel_entity_id = user_input.get(CONF_SUBLABEL_ENTITY_ID)
                if sublabel_entity_id:
                    self._new_tile_config[CONF_SUBLABEL_ENTITY_ID] = sublabel_entity_id
                if self._new_type == "indicator":
                    secondary_entity_id = user_input.get(CONF_INDICATOR_SECONDARY_ENTITY_ID)
                    if secondary_entity_id:
                        self._new_tile_config[CONF_INDICATOR_SECONDARY_ENTITY_ID] = secondary_entity_id
                # Go to background image selection step
                return await self.async_step_add_tile_background()

            tile_options = [
                {"value": str(i), "label": f"Position {i}"}
                for i in free
            ]
            # Tile types may narrow the picker past the domain, so a door tile
            # offers door and window contacts rather than every binary_sensor.
            entity_config: dict[str, Any] = {"domain": definition["domain"]}
            if definition.get("device_class"):
                entity_config["device_class"] = definition["device_class"]
            # Light capability and availability can only be judged by looking at
            # live state, which the selector cannot do - so pass it the resolved
            # list instead. Falling back to the plain domain picker when nothing
            # qualifies beats showing the user an empty dropdown.
            eligible = eligible_entity_ids(self.hass, definition)
            if eligible:
                entity_config["include_entities"] = eligible
            else:
                _LOGGER.warning(
                    "No available entity is compatible with tile type '%s'; "
                    "falling back to an unfiltered %s picker",
                    self._new_type,
                    definition["domain"],
                )
            schema_dict: dict[Any, Any] = {
                vol.Required(
                    CONF_TILE, default=str(free[0])
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=tile_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(**entity_config)
                ),
                vol.Optional(CONF_LABEL, default=""): selector.TextSelector(),
                vol.Optional(
                    CONF_ICON, default=definition["icon"]
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._build_icon_options(),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                # Common capability: optional subLabel source, any tile type,
                # any domain (e.g. a sensor's value, another entity's state).
                vol.Optional(CONF_SUBLABEL_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig()
                ),
            }
            if self._new_type == "indicator":
                # indicator tile: optional second sensor shown alongside the
                # primary one (e.g. temperature + humidity in one tile). It
                # renders through the same numeric-only field, so it gets the
                # same eligibility list as the primary.
                secondary_config: dict[str, Any] = {"domain": "sensor"}
                if eligible:
                    secondary_config["include_entities"] = eligible
                schema_dict[
                    vol.Optional(CONF_INDICATOR_SECONDARY_ENTITY_ID)
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(**secondary_config)
                )
            schema = vol.Schema(schema_dict)
            
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
                new_options = dict(self._entry.options)
                new_options[CONF_TILES] = self._tiles
                new_options[CONF_SCREEN_NAMES] = self._screen_names
                return self.async_create_entry(title="", data=new_options)
            
            # Get background image options from the shared library - every
            # image added on ANY panel is available here, not just this one.
            library = self._get_library()
            background_images = []
            if library:
                background_images = [
                    {"value": img["name"], "label": img["name"]}
                    for img in library.list_images()
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
            new_options = dict(self._entry.options)
            new_options[CONF_TILES] = self._tiles
            return self.async_create_entry(title="", data=new_options)

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
        """Menu: add a new background image, or delete an existing one."""
        return self.async_show_menu(
            step_id="manage_background_images",
            menu_options=["add_background_image", "delete_background_image"],
        )

    async def async_step_add_background_image(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a background image to the shared library - paste base64 from
        the OXRS Asset Generator. Available to every configured panel."""
        try:
            if user_input is not None:
                image_name = user_input.get("image_name", "").strip()
                image_base64 = user_input.get("image_base64", "").strip()

                if not image_name:
                    return self.async_show_form(
                        step_id="add_background_image",
                        data_schema=self._build_add_media_schema(),
                        errors={"base": "no_name"},
                    )
                if not image_base64:
                    return self.async_show_form(
                        step_id="add_background_image",
                        data_schema=self._build_add_media_schema(),
                        errors={"base": "no_file"},
                    )
                if image_name.startswith("_"):
                    return self.async_show_form(
                        step_id="add_background_image",
                        data_schema=self._build_add_media_schema(),
                        errors={"base": "invalid_image_name"},
                    )

                image_bytes, fmt, error_key = _decode_and_validate_base64_image(image_base64)
                if error_key:
                    return self.async_show_form(
                        step_id="add_background_image",
                        data_schema=self._build_add_media_schema(),
                        errors={"base": error_key},
                    )

                library = self._get_library()
                if library is None:
                    _LOGGER.error("Shared media library not available")
                    return self.async_abort(reason="invalid_format")

                image_id = hashlib.md5(image_base64.encode()).hexdigest()[:12]
                success = await library.add_image(image_id, image_name, image_bytes, fmt)
                if not success:
                    return self.async_show_form(
                        step_id="add_background_image",
                        data_schema=self._build_add_media_schema(),
                        errors={"base": "image_error"},
                    )

                return self.async_abort(reason="image_uploaded")

            return self.async_show_form(
                step_id="add_background_image",
                data_schema=self._build_add_media_schema(),
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_background_image: {err}", exc_info=True)
            return self.async_abort(reason="invalid_format")

    async def async_step_delete_background_image(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete a background image from the shared library.

        Note: any tile (on any panel) still referencing this image by name
        will simply stop receiving it on the next config push - it degrades
        gracefully rather than breaking, but won't show a background until
        a new one is chosen for that tile.
        """
        library = self._get_library()
        images = library.list_images() if library else []
        if not images:
            return self.async_abort(reason="no_images")

        if user_input is not None:
            await library.delete_image(user_input["image_id"])
            return self.async_abort(reason="image_deleted")

        options = [{"value": img["id"], "label": img["name"]} for img in images]
        schema = vol.Schema(
            {
                vol.Required("image_id"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="delete_background_image", data_schema=schema)

    async def async_step_manage_custom_icons(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Menu: add a new custom icon, or delete an existing one."""
        return self.async_show_menu(
            step_id="manage_custom_icons",
            menu_options=["add_custom_icon", "delete_custom_icon"],
        )

    async def async_step_add_custom_icon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a custom icon to the shared library - paste base64 PNG from
        the OXRS Asset Generator (change addImage to addIcon in its output).
        Available to every configured panel, grouped by category in the
        icon picker."""
        try:
            if user_input is not None:
                icon_name = user_input.get("icon_name", "").strip()
                icon_base64 = user_input.get("icon_base64", "").strip()
                category = user_input.get("category", "misc")

                if not icon_name:
                    return self.async_show_form(
                        step_id="add_custom_icon",
                        data_schema=self._build_add_icon_schema(),
                        errors={"base": "no_name"},
                    )
                if not icon_base64:
                    return self.async_show_form(
                        step_id="add_custom_icon",
                        data_schema=self._build_add_icon_schema(),
                        errors={"base": "no_file"},
                    )
                if icon_name.startswith("_"):
                    return self.async_show_form(
                        step_id="add_custom_icon",
                        data_schema=self._build_add_icon_schema(),
                        errors={"base": "invalid_image_name"},
                    )

                icon_bytes, _fmt, error_key = _decode_and_validate_base64_image(
                    icon_base64, require_png=True
                )
                if error_key:
                    return self.async_show_form(
                        step_id="add_custom_icon",
                        data_schema=self._build_add_icon_schema(),
                        errors={"base": error_key},
                    )

                library = self._get_library()
                if library is None:
                    _LOGGER.error("Shared media library not available")
                    return self.async_abort(reason="invalid_format")

                icon_id = hashlib.md5(icon_base64.encode()).hexdigest()[:12]
                success = await library.add_icon(icon_id, icon_name, icon_bytes, category)
                if not success:
                    return self.async_show_form(
                        step_id="add_custom_icon",
                        data_schema=self._build_add_icon_schema(),
                        errors={"base": "image_error"},
                    )

                return self.async_abort(reason="icon_uploaded")

            return self.async_show_form(
                step_id="add_custom_icon",
                data_schema=self._build_add_icon_schema(),
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_custom_icon: {err}", exc_info=True)
            return self.async_abort(reason="invalid_format")

    async def async_step_delete_custom_icon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete a custom icon from the shared library."""
        library = self._get_library()
        icons = library.list_icons() if library else []
        if not icons:
            return self.async_abort(reason="no_icons")

        if user_input is not None:
            await library.delete_icon(user_input["icon_id"])
            return self.async_abort(reason="icon_deleted")

        options = [
            {
                "value": icon["id"],
                "label": f"{ICON_CATEGORIES.get(icon.get('category', ''), 'Other')}: {icon['name']}",
            }
            for icon in icons
        ]
        schema = vol.Schema(
            {
                vol.Required("icon_id"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="delete_custom_icon", data_schema=schema)

    def _build_add_media_schema(self) -> vol.Schema:
        """Schema for pasting a background image's base64 string."""
        return vol.Schema(
            {
                vol.Required("image_name"): selector.TextSelector(),
                vol.Required("image_base64"): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )

    def _build_add_icon_schema(self) -> vol.Schema:
        """Schema for pasting a custom icon's base64 PNG string + category."""
        category_options = [
            {"value": key, "label": label} for key, label in ICON_CATEGORIES.items()
        ]
        return vol.Schema(
            {
                vol.Required("icon_name"): selector.TextSelector(),
                vol.Required("icon_base64"): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Required("category", default="misc"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=category_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
