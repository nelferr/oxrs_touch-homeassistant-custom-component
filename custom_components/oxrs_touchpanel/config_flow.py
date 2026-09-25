"""Config and options flows for the OXRS Touch Panel integration."""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

_LOGGER = logging.getLogger(__name__)

from .boards import (
    BOARDS,
    OTHER_BOARD,
    board_label,
    describe,
    hardware_from_adopt,
    layout_from_data,
    new_entry_data,
)
from .colors import BLACK, normalize_rgb
from .grid import (
    ONE,
    free_anchors,
    is_size_tested,
    larger_sizes,
    occupied,
    parse_size_value,
    size_label,
    size_value,
    tile_span,
)
from .const import (
    ALBUM_ART_SIZE,
    BUILTIN_ICONS,
    CONF_ALBUM_ART,
    CONF_ALBUM_ART_BUDGET,
    CONF_ALBUM_ART_MAX_SOURCE,
    CONF_ALBUM_ART_TEXT,
    CONF_ALBUM_ART_ZOOM,
    CONF_BACKGROUND_COLOR,
    CONF_CLIENT_ID,
    CONF_ENTITY_ID,
    CONF_HARDWARE,
    CONF_ICON,
    CONF_ICON_ON_COLOR,
    CONF_INDICATOR_SECONDARY_ENTITY_ID,
    CONF_LABEL,
    CONF_NAME,
    CONF_PANEL_SETTINGS,
    CONF_PLAYLISTS,
    CONF_SCREEN,
    CONF_SCREEN_COLORS,
    CONF_SCREEN_NAMES,
    CONF_SPAN,
    CONF_SUBLABEL_ENTITY_ID,
    CONF_TILE,
    CONF_TILES,
    CONF_TYPE,
    DEFAULT_ALBUM_ART_BUDGET,
    DEFAULT_ALBUM_ART_MAX_SOURCE,
    DEFAULT_ALBUM_ART_TEXT,
    DEFAULT_ALBUM_ART_ZOOM,
    DEFAULT_BACKGROUND_COLOR,
    DEFAULT_ICON_ON_COLOR,
    DOMAIN,
    FIELD_LARGER,
    LIBRARY_DATA_KEY,
    MAX_ALBUM_ART_BUDGET,
    MAX_ALBUM_ART_MAX_SOURCE,
    MAX_ALBUM_ART_ZOOM,
    MAX_PLAYLISTS,
    MIN_ALBUM_ART_BUDGET,
    MIN_ALBUM_ART_MAX_SOURCE,
    MIN_ALBUM_ART_ZOOM,
    PANEL_SETTINGS,
)
from .library import (
    ICON_CATEGORIES,
    MAX_ENCODED_SIZE,
    MAX_ENCODED_SIZE_HARD,
    SharedMediaLibrary,
)
from .tiles import (
    TILE_TYPES,
    default_icon,
    eligible_entity_ids,
    playlists_from_library,
    suggested_icons,
)


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

    # OXRS docs: "encoded image should not exceed 4KB - TBC". Over that is
    # allowed but flagged, since the real ceiling is only knowable on hardware
    # and 4KB is too little for a photographic image at tile size.
    if len(image_base64) > MAX_ENCODED_SIZE_HARD:
        _LOGGER.warning(
            "Base64 string too large: %d chars (hard limit %d)",
            len(image_base64),
            MAX_ENCODED_SIZE_HARD,
        )
        return None, "", "image_too_large"
    if len(image_base64) > MAX_ENCODED_SIZE:
        _LOGGER.warning(
            "Base64 string is %d chars, above the ~%d the OXRS docs call safe. "
            "The panel may ignore the image or restart.",
            len(image_base64),
            MAX_ENCODED_SIZE,
        )

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
        self._hardware: str | None = None

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
        # The adopt message names the board, which decides the panel's tile grid.
        self._hardware = hardware_from_adopt(discovery_info.payload)
        self.context["title_placeholders"] = {"name": client_id}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered panel."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._client_id,
                data=new_entry_data(self._client_id, self._hardware),
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._client_id,
                "board": describe(self._hardware),
            },
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
            hardware = user_input.get(CONF_HARDWARE)
            return self.async_create_entry(
                title=user_input.get(CONF_NAME) or client_id,
                data=new_entry_data(
                    client_id, None if hardware in (None, OTHER_BOARD) else hardware
                ),
            )
        # Added by hand there is no discovery message to read the board from, so
        # ask. The board sets how many tiles each screen has.
        schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): str,
                vol.Optional(CONF_NAME): str,
                vol.Required(CONF_HARDWARE, default=OTHER_BOARD): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            *(
                                {"value": hw, "label": board_label(board)}
                                for hw, board in BOARDS.items()
                            ),
                            {"value": OTHER_BOARD, "label": "Other or not listed (3 × 3 tiles)"},
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
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
        # The screen whose name and colour are being edited.
        self._edit_screen: int = 1
        # Index into self._tiles of the tile being edited. The draft lives in
        # _new_tile_config and only replaces the original on the last step, so
        # closing the dialog part-way changes nothing.
        self._edit_index: int = -1
        self._new_tile_config: dict[str, Any] | None = None
        # Playlists fetched from Music Assistant for the tile being added, kept
        # so a validation error re-shows the form without fetching again.
        self._playlist_choices: list[dict[str, str]] | None = None

    def _grid(self) -> tuple[int, int]:
        """(columns, rows) of THIS panel's screens."""
        layout = layout_from_data(self._entry.data)
        return layout["horizontal"], layout["vertical"]

    def _occupied(self, screen: int, skip: int | None = None) -> set[int]:
        """Every position on a screen covered by a tile, at its full size.

        skip is the index of the tile being edited, so it does not block itself.
        """
        cols, rows = self._grid()
        return occupied(self._tiles, screen, cols, rows, skip=skip)

    def _get_library(self) -> SharedMediaLibrary | None:
        """Return the shared media library, if the integration has finished
        loading it (it always will have, by the time an options flow for an
        already-configured panel can be opened)."""
        return self.hass.data.get(LIBRARY_DATA_KEY)

    def _available_icon_names(self) -> set[str]:
        """Every icon a tile can use right now: built-ins plus the library."""
        names = set(BUILTIN_ICONS)
        library = self._get_library()
        if library:
            names.update(icon["name"] for icon in library.list_icons())
        return names

    def _build_icon_options(self, suggested: list[str] | None = None) -> list[dict[str, str]]:
        """Combine firmware built-in icons with custom icons from the shared
        library into one selector option list.

        The tile type's suggested icons lead, labelled as such and not repeated
        further down. Custom icon labels are prefixed with their category and
        sorted by it, so each category reads as a block - without relying on
        disabled separator rows (which don't render consistently across HA
        frontend versions)."""
        suggested = suggested or []
        options = [{"value": name, "label": f"Suggested: {name}"} for name in suggested]
        options.extend(
            {"value": name, "label": name} for name in BUILTIN_ICONS if name not in suggested
        )
        library = self._get_library()
        if library:
            custom = [
                (ICON_CATEGORIES.get(icon.get("category", ""), "Other"), icon["name"])
                for icon in library.list_icons()
                if icon["name"] not in suggested
            ]
            options.extend(
                {"value": name, "label": f"{category_label}: {name}"}
                for category_label, name in sorted(custom)
            )
        return options

    def _integration_loaded(self, domain: str) -> bool:
        return any(
            entry.state is ConfigEntryState.LOADED
            for entry in self.hass.config_entries.async_entries(domain)
        )

    async def _async_fetch_playlists(self, entity_id: str) -> list[dict[str, str]] | None:
        """Every playlist in the Music Assistant library behind a player.

        Returns None when the library can't be reached, and an empty list when
        it simply has no playlists, so the two get different messages.
        """
        registry_entry = er.async_get(self.hass).async_get(entity_id)
        if registry_entry is None or registry_entry.config_entry_id is None:
            _LOGGER.warning("%s has no Music Assistant config entry", entity_id)
            return None
        try:
            response = await self.hass.services.async_call(
                "music_assistant",
                "get_library",
                {
                    "config_entry_id": registry_entry.config_entry_id,
                    "media_type": "playlist",
                    "order_by": "name",
                    "limit": 500,
                },
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not list Music Assistant playlists: %s", err)
            return None
        return playlists_from_library(response)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the tile-management menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_tile",
                "edit_tile",
                "remove_tile",
                "rename_screen",
                "manage_background_images",
                "manage_custom_icons",
                "panel_settings",
                "album_art_settings",
            ],
        )

    async def async_step_panel_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the panel's timeouts, brightness, sensor interval and background.

        These are the firmware's own settings, the same ones its admin page
        offers, and each is pre-filled with the firmware default. The numeric
        fields are built from PANEL_SETTINGS so their limits are the ones the
        hub clamps to; the background colour uses HA's colour picker rather
        than three separate 0-255 numbers.
        """
        stored = self._entry.options.get(CONF_PANEL_SETTINGS) or {}
        if user_input is not None:
            options = dict(self._entry.options)
            options[CONF_PANEL_SETTINGS] = {
                key: int(user_input[key]) for key in PANEL_SETTINGS if key in user_input
            }
            for key in (CONF_BACKGROUND_COLOR, CONF_ICON_ON_COLOR):
                if key in user_input:
                    options[key] = [int(c) for c in user_input[key]]
            return self.async_create_entry(title="", data=options)

        fields: dict[Any, Any] = {
            vol.Required(
                key, default=stored.get(key, default)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=low,
                    max=high,
                    step=1,
                    mode="box",
                    unit_of_measurement=(
                        "%" if key.startswith("tileBrightness") else "seconds"
                    ),
                )
            )
            for key, (default, low, high) in PANEL_SETTINGS.items()
        }
        for key, default in (
            (CONF_BACKGROUND_COLOR, DEFAULT_BACKGROUND_COLOR),
            (CONF_ICON_ON_COLOR, DEFAULT_ICON_ON_COLOR),
        ):
            # normalize_rgb, not a bare list(): a hand-edited option that is not
            # a colour would otherwise reach the picker and fail its validation.
            fields[
                vol.Required(
                    key,
                    default=list(normalize_rgb(self._entry.options.get(key)) or default),
                )
            ] = selector.ColorRGBSelector()
        return self.async_show_form(
            step_id="panel_settings", data_schema=vol.Schema(fields)
        )

    async def async_step_album_art_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set how album art is sized for this panel.

        Panel-level rather than per-tile: the limits are properties of the
        firmware's MQTT buffer and memory, not of any one tile. Raising the byte
        budget past what the firmware accepts makes art silently fail to draw, and
        raising the largest image edge past what the panel can hold risks a crash
        that repeats on every reconnect, so the defaults sit at what is known to
        work. They are settings, not constants, so they can be tuned once tested
        on real hardware.
        """
        if user_input is not None:
            options = dict(self._entry.options)
            options[CONF_ALBUM_ART_BUDGET] = int(user_input[CONF_ALBUM_ART_BUDGET])
            options[CONF_ALBUM_ART_ZOOM] = int(user_input[CONF_ALBUM_ART_ZOOM])
            options[CONF_ALBUM_ART_MAX_SOURCE] = int(user_input[CONF_ALBUM_ART_MAX_SOURCE])
            options[CONF_ALBUM_ART_TEXT] = bool(user_input.get(CONF_ALBUM_ART_TEXT))
            return self.async_create_entry(title="", data=options)

        current = self._entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ALBUM_ART_BUDGET,
                    default=current.get(CONF_ALBUM_ART_BUDGET, DEFAULT_ALBUM_ART_BUDGET),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_ALBUM_ART_BUDGET,
                        max=MAX_ALBUM_ART_BUDGET,
                        step=256,
                        mode="box",
                        unit_of_measurement="bytes",
                    )
                ),
                vol.Required(
                    CONF_ALBUM_ART_ZOOM,
                    default=current.get(CONF_ALBUM_ART_ZOOM, DEFAULT_ALBUM_ART_ZOOM),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_ALBUM_ART_ZOOM,
                        max=MAX_ALBUM_ART_ZOOM,
                        step=5,
                        mode="box",
                        unit_of_measurement="%",
                    )
                ),
                vol.Required(
                    CONF_ALBUM_ART_MAX_SOURCE,
                    default=current.get(CONF_ALBUM_ART_MAX_SOURCE, DEFAULT_ALBUM_ART_MAX_SOURCE),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_ALBUM_ART_MAX_SOURCE,
                        max=MAX_ALBUM_ART_MAX_SOURCE,
                        step=10,
                        mode="box",
                        unit_of_measurement="px",
                    )
                ),
                vol.Required(
                    CONF_ALBUM_ART_TEXT,
                    default=current.get(CONF_ALBUM_ART_TEXT, DEFAULT_ALBUM_ART_TEXT),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="album_art_settings",
            data_schema=schema,
            description_placeholders={
                "default": str(DEFAULT_ALBUM_ART_BUDGET),
                "size": str(ALBUM_ART_SIZE),
            },
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
        """Choose which screen to rename or recolour."""
        try:
            known_screens = sorted({t[CONF_SCREEN] for t in self._tiles})
            if not known_screens:
                return self.async_abort(reason="no_screens")

            if user_input is not None:
                self._edit_screen = int(user_input["screen"])
                return await self.async_step_screen_appearance()

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
                }
            )
            return self.async_show_form(step_id="rename_screen", data_schema=schema)
        except Exception as err:
            _LOGGER.error(f"Error in async_step_rename_screen: {err}", exc_info=True)
            return self.async_abort(reason="invalid_format")

    async def async_step_screen_appearance(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the chosen screen's name and background colour.

        A separate step from choosing the screen, so both fields can show the
        screen's CURRENT values. In one combined form the colour picker could
        not know which screen was selected, and saving a rename would have
        silently reset the colour to black.
        """
        try:
            screen = self._edit_screen
            key = str(screen)
            colors = dict(self._entry.options.get(CONF_SCREEN_COLORS) or {})

            if user_input is not None:
                new_name = user_input.get("screen_name", "").strip()
                if new_name:
                    self._screen_names[key] = new_name
                new_color = normalize_rgb(user_input.get("screen_color"))
                # Black is the firmware's "unset" - the screen then follows the
                # panel-wide colour - so it is stored as no colour at all.
                if new_color is not None and new_color != BLACK:
                    colors[key] = list(new_color)
                else:
                    colors.pop(key, None)
                new_options = dict(self._entry.options)
                new_options[CONF_SCREEN_NAMES] = self._screen_names
                new_options[CONF_SCREEN_COLORS] = colors
                return self.async_create_entry(title="", data=new_options)

            schema = vol.Schema(
                {
                    vol.Optional(
                        "screen_name",
                        default=self._screen_names.get(key, f"Screen {screen}"),
                    ): selector.TextSelector(),
                    vol.Required(
                        "screen_color",
                        default=list(normalize_rgb(colors.get(key)) or BLACK),
                    ): selector.ColorRGBSelector(),
                }
            )
            return self.async_show_form(
                step_id="screen_appearance",
                data_schema=schema,
                description_placeholders={"screen": key},
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_screen_appearance: {err}", exc_info=True)
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

            # A type tied to an integration (Music Assistant playlists) is only
            # offered while that integration is loaded; otherwise its entity
            # picker would be empty.
            type_options = [
                {"value": key, "label": defn["label"]}
                for key, defn in TILE_TYPES.items()
                if not defn.get("integration")
                or self._integration_loaded(defn["integration"])
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

    @staticmethod
    def _apply_details_input(
        tile: dict[str, Any], tile_type: str, user_input: dict[str, Any]
    ) -> None:
        """Copy the optional fields of the tile details form onto a tile config.

        Shared by adding and editing so the two cannot drift. A field left empty
        is REMOVED from the tile rather than kept, which is what lets an edit
        clear a sub-label source or turn album art off.
        """

        def set_or_clear(key: str, value: Any) -> None:
            if value:
                tile[key] = value
            else:
                tile.pop(key, None)

        set_or_clear(CONF_SUBLABEL_ENTITY_ID, user_input.get(CONF_SUBLABEL_ENTITY_ID))
        # Black means "no colour of its own": the tile follows its screen.
        color = normalize_rgb(user_input.get(CONF_BACKGROUND_COLOR))
        set_or_clear(
            CONF_BACKGROUND_COLOR,
            list(color) if color is not None and color != BLACK else None,
        )
        if tile_type == "indicator":
            set_or_clear(
                CONF_INDICATOR_SECONDARY_ENTITY_ID,
                user_input.get(CONF_INDICATOR_SECONDARY_ENTITY_ID),
            )
        if tile_type == "transport":
            set_or_clear(CONF_ALBUM_ART, True if user_input.get(CONF_ALBUM_ART) else None)

    def _tile_details_schema(
        self,
        tile_type: str,
        free: list[int],
        current: dict[str, Any] | None = None,
        larger: bool = False,
    ) -> vol.Schema:
        """The details form for a tile: position, entity, label, icon and extras.

        Used for adding (current is None, everything starts blank) and for
        editing (current is the tile being edited, and every field starts on its
        present value). One builder, so the two forms cannot disagree.
        """
        definition = TILE_TYPES[tile_type]
        editing = current is not None
        current = current or {}

        # Tile types may narrow the picker past the domain, so a door tile
        # offers door and window contacts rather than every binary_sensor.
        entity_config: dict[str, Any] = {"domain": definition["domain"]}
        if definition.get("device_class"):
            entity_config["device_class"] = definition["device_class"]
        if definition.get("integration"):
            entity_config["integration"] = definition["integration"]
        # Light capability and availability can only be judged by looking at
        # live state, which the selector cannot do - so pass it the resolved
        # list instead. Falling back to the plain domain picker when nothing
        # qualifies beats showing the user an empty dropdown.
        eligible = eligible_entity_ids(self.hass, definition)
        if eligible:
            # HA validates a submitted entity against include_entities, and the
            # list drops anything unavailable. When editing, the tile's own
            # entity may be unavailable right now; leaving it out would make the
            # form impossible to submit without changing it.
            current_entity = current.get(CONF_ENTITY_ID)
            entity_config["include_entities"] = (
                eligible + [current_entity]
                if current_entity and current_entity not in eligible
                else eligible
            )
        else:
            _LOGGER.warning(
                "No available entity is compatible with tile type '%s'; "
                "falling back to an unfiltered %s picker",
                tile_type,
                definition["domain"],
            )

        available_icons = self._available_icon_names()
        icon_options = self._build_icon_options(suggested_icons(definition, available_icons))
        icon_default = (
            current.get(CONF_ICON) or definition["icon"]
            if editing
            else default_icon(definition, available_icons)
        )
        # SelectSelector rejects a value that is not one of its options. A tile
        # can outlive its icon (the library lets one be deleted), so keep the
        # current one selectable rather than making the form unsubmittable.
        if editing and icon_default not in {o["value"] for o in icon_options}:
            icon_options.append(
                {"value": icon_default, "label": f"{icon_default} (not in the library)"}
            )

        def optional_entity(key: str, config: dict[str, Any]) -> tuple[Any, Any]:
            # suggested_value rather than default: it pre-fills the field but
            # still lets the user clear it, which a default would not.
            value = current.get(key)
            marker = (
                vol.Optional(key, description={"suggested_value": value})
                if editing and value
                else vol.Optional(key)
            )
            return marker, selector.EntitySelector(selector.EntitySelectorConfig(**config))

        schema_dict: dict[Any, Any] = {
            vol.Required(
                CONF_TILE,
                default=str(current[CONF_TILE] if editing else free[0]),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": str(i), "label": f"Position {i}"} for i in free
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            (
                vol.Required(CONF_ENTITY_ID, default=current[CONF_ENTITY_ID])
                if editing
                else vol.Required(CONF_ENTITY_ID)
            ): selector.EntitySelector(selector.EntitySelectorConfig(**entity_config)),
            vol.Optional(
                CONF_LABEL, default=current.get(CONF_LABEL, "")
            ): selector.TextSelector(),
            vol.Optional(CONF_ICON, default=icon_default): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=icon_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
        # Common capability: optional subLabel source, any tile type, any domain
        # (e.g. a sensor's value, another entity's state).
        marker, sel = optional_entity(CONF_SUBLABEL_ENTITY_ID, {})
        schema_dict[marker] = sel
        # Left on black, the tile follows its screen's colour.
        schema_dict[
            vol.Required(
                CONF_BACKGROUND_COLOR,
                default=list(normalize_rgb(current.get(CONF_BACKGROUND_COLOR)) or BLACK),
            )
        ] = selector.ColorRGBSelector()
        # Ticking this leads to a step that lists the sizes that fit at the chosen
        # position. It is a checkbox rather than a size field because the choices
        # depend on the position, and a form cannot change one field's options
        # from another - and an extra step for every tile added would be a tax
        # on the common case.
        schema_dict[vol.Optional(FIELD_LARGER, default=larger)] = selector.BooleanSelector()
        if tile_type == "transport":
            # transport tile: optionally show the player's current cover as the
            # tile background. The firmware needs non-empty text to hide an
            # icon, so art and the _play/_pause icon cannot both be on screen -
            # the tile keeps the title as its subLabel instead.
            schema_dict[
                vol.Optional(CONF_ALBUM_ART, default=bool(current.get(CONF_ALBUM_ART)))
            ] = selector.BooleanSelector()
        if tile_type == "indicator":
            # indicator tile: optional second sensor shown alongside the primary
            # one (e.g. temperature + humidity in one tile). It renders through
            # the same numeric-only field, so it gets the same eligibility list
            # as the primary.
            secondary_config: dict[str, Any] = {"domain": "sensor"}
            if eligible:
                secondary = current.get(CONF_INDICATOR_SECONDARY_ENTITY_ID)
                secondary_config["include_entities"] = (
                    eligible + [secondary]
                    if secondary and secondary not in eligible
                    else eligible
                )
            marker, sel = optional_entity(
                CONF_INDICATOR_SECONDARY_ENTITY_ID, secondary_config
            )
            schema_dict[marker] = sel
        return vol.Schema(schema_dict)

    async def async_step_add_tile_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: pick a FREE position, the entity, label and icon."""
        try:
            _LOGGER.debug(f"async_step_add_tile_details called with input: {list(user_input.keys()) if user_input else 'None'}")

            assert self._new_type is not None
            definition = TILE_TYPES[self._new_type]
            _LOGGER.debug(f"Tile type definition: {self._new_type}")

            cols, rows = self._grid()
            # Cells covered by tiles already here, each at its FULL size: a large
            # tile blocks every cell it covers, not just the one it is anchored on.
            taken = self._occupied(self._new_screen)
            free = free_anchors(taken, cols, rows)
            placeholders = {
                "type": definition["label"],
                "screen": str(self._new_screen),
            }

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
                self._apply_details_input(self._new_tile_config, self._new_type, user_input)
                if user_input.get(FIELD_LARGER):
                    if not larger_sizes(self._new_tile_config[CONF_TILE], taken, cols, rows):
                        return self.async_show_form(
                            step_id="add_tile_details",
                            data_schema=self._tile_details_schema(
                                self._new_type, free, current=self._new_tile_config, larger=True
                            ),
                            errors={FIELD_LARGER: "no_larger_size"},
                            description_placeholders=placeholders,
                        )
                    return await self.async_step_add_tile_size()
                return await self._async_after_add_size()

            _LOGGER.debug("Showing tile details form")
            return self.async_show_form(
                step_id="add_tile_details",
                data_schema=self._tile_details_schema(self._new_type, free),
                description_placeholders=placeholders,
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_add_tile_details: {err}", exc_info=True)
            return self.async_abort(reason="invalid_details")

    async def _async_playlist_step(
        self,
        *,
        step_id: str,
        user_input: dict[str, Any] | None,
        next_step: Any,
        preselect: list[str] | None = None,
        keep_on_failure: bool = False,
    ) -> ConfigFlowResult:
        """Pick the playlists a playlists tile lists, for adding or editing.

        preselect ticks the playlists a tile already has. keep_on_failure is for
        editing: when Music Assistant cannot be reached the step is skipped and
        the tile keeps the playlists it has, so its other settings can still be
        changed. Adding has nothing to fall back on and aborts instead.
        """
        assert self._new_tile_config is not None

        if self._playlist_choices is None:
            choices = await self._async_fetch_playlists(self._new_tile_config[CONF_ENTITY_ID])
            if not choices:
                if keep_on_failure:
                    _LOGGER.warning(
                        "Could not list Music Assistant playlists; keeping the tile's current ones"
                    )
                    return await next_step()
                return self.async_abort(
                    reason="music_assistant_unavailable" if choices is None else "no_playlists"
                )
            self._playlist_choices = choices

        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = user_input.get(CONF_PLAYLISTS) or []
            if not chosen:
                errors["base"] = "no_playlists_selected"
            elif len(chosen) > MAX_PLAYLISTS:
                errors["base"] = "too_many_playlists"
            else:
                by_uri = {p["uri"]: p for p in self._playlist_choices}
                self._new_tile_config[CONF_PLAYLISTS] = [
                    by_uri[uri] for uri in chosen if uri in by_uri
                ]
                self._playlist_choices = None
                return await next_step()

        if preselect is None:
            key = vol.Required(CONF_PLAYLISTS)
        else:
            # Only tick playlists that still exist: one deleted from Music
            # Assistant is not among the options, and a default outside the
            # options would fail the form's own validation.
            known = {p["uri"] for p in self._playlist_choices}
            key = vol.Required(
                CONF_PLAYLISTS, default=[uri for uri in preselect if uri in known]
            )
        schema = vol.Schema(
            {
                key: selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": p["uri"], "label": p["name"]}
                            for p in self._playlist_choices
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders={"max": str(MAX_PLAYLISTS)},
        )

    async def async_step_add_tile_playlists(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b, playlist tiles only: pick the playlists the panel lists."""
        return await self._async_playlist_step(
            step_id="add_tile_playlists",
            user_input=user_input,
            next_step=self.async_step_add_tile_background,
        )

    def _background_image_choices(self) -> list[dict[str, str]]:
        """Every image in the shared library - added on ANY panel, not just this one."""
        library = self._get_library()
        if not library:
            return []
        return [{"value": img["name"], "label": img["name"]} for img in library.list_images()]

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

            background_images = self._background_image_choices()

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

    # ── editing a tile ─────────────────────────────────────────────────────
    # Choose the tile, change its details, (playlists tiles) its playlists, then
    # its background image. The screen and the tile type stay fixed - they are
    # what the tile IS, and everything else about it hangs off them - so changing
    # either means removing the tile and adding a new one.

    @staticmethod
    def _tile_choice_label(tile: dict[str, Any]) -> str:
        w, h = tile_span(tile.get(CONF_SPAN))
        size = f" {w}×{h}" if (w, h) != ONE else ""
        return (
            f"S{tile[CONF_SCREEN]}·T{tile[CONF_TILE]}{size} "
            f"[{tile.get(CONF_TYPE, '')}] "
            f"{tile.get(CONF_LABEL) or ''} ({tile.get(CONF_ENTITY_ID, 'N/A')})"
        )

    async def async_step_edit_tile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the tile to edit."""
        # Only tiles of a known type can be edited: the edit form is built from
        # the type's definition.
        editable = [
            (index, tile)
            for index, tile in enumerate(self._tiles)
            if tile.get(CONF_TYPE) in TILE_TYPES
        ]
        if not editable:
            return self.async_abort(reason="no_editable_tiles")

        if user_input is not None:
            self._edit_index = int(user_input["index"])
            tile = self._tiles[self._edit_index]
            # Work on a copy: nothing is saved until the last step.
            self._new_tile_config = dict(tile)
            self._new_type = tile[CONF_TYPE]
            self._new_screen = tile[CONF_SCREEN]
            self._playlist_choices = None
            return await self.async_step_edit_tile_details()

        schema = vol.Schema(
            {
                vol.Required("index"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": str(index), "label": self._tile_choice_label(tile)}
                            for index, tile in editable
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="edit_tile", data_schema=schema)

    async def async_step_edit_tile_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the tile's position, entity, label, icon and extras."""
        try:
            assert self._new_tile_config is not None and self._new_type is not None
            definition = TILE_TYPES[self._new_type]
            draft = self._new_tile_config

            cols, rows = self._grid()
            # Cells covered by OTHER tiles on the screen, each at its full size;
            # the tile's own cells are free for it.
            taken = self._occupied(self._new_screen, skip=self._edit_index)
            free = free_anchors(taken, cols, rows)
            placeholders = {
                "type": definition["label"],
                "screen": str(self._new_screen),
            }

            if user_input is not None:
                draft[CONF_TILE] = int(user_input[CONF_TILE])
                draft[CONF_ENTITY_ID] = user_input[CONF_ENTITY_ID]
                draft[CONF_LABEL] = user_input.get(CONF_LABEL, "")
                draft[CONF_ICON] = user_input.get(CONF_ICON, definition["icon"])
                self._apply_details_input(draft, self._new_type, user_input)
                if user_input.get(FIELD_LARGER):
                    if not larger_sizes(draft[CONF_TILE], taken, cols, rows):
                        return self.async_show_form(
                            step_id="edit_tile_details",
                            data_schema=self._tile_details_schema(
                                self._new_type, free, current=draft, larger=True
                            ),
                            errors={FIELD_LARGER: "no_larger_size"},
                            description_placeholders=placeholders,
                        )
                    return await self.async_step_edit_tile_size()
                # Unticked: the tile goes back to one cell.
                draft.pop(CONF_SPAN, None)
                return await self._async_after_edit_size()

            return self.async_show_form(
                step_id="edit_tile_details",
                data_schema=self._tile_details_schema(
                    self._new_type,
                    free,
                    current=draft,
                    larger=tile_span(draft.get(CONF_SPAN)) != ONE,
                ),
                description_placeholders=placeholders,
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_edit_tile_details: {err}", exc_info=True)
            return self.async_abort(reason="invalid_details")

    async def _async_size_step(
        self,
        *,
        step_id: str,
        user_input: dict[str, Any] | None,
        next_step: Any,
        skip: int | None = None,
    ) -> ConfigFlowResult:
        """Choose the size of the tile being added or edited.

        Offers only sizes that fit where the tile is, which is what keeps one tile
        from ever covering another: the firmware stacks overlapping tiles rather
        than refusing them.
        """
        assert self._new_tile_config is not None
        draft = self._new_tile_config
        cols, rows = self._grid()
        taken = self._occupied(draft[CONF_SCREEN], skip=skip)
        sizes = larger_sizes(draft[CONF_TILE], taken, cols, rows)
        if not sizes:
            # Nothing larger fits (the position changed since the last step).
            draft.pop(CONF_SPAN, None)
            return await next_step()

        style = TILE_TYPES[draft[CONF_TYPE]]["style"]
        tested = is_size_tested(style)

        if user_input is not None:
            chosen = parse_size_value(user_input.get("size"))
            draft[CONF_SPAN] = list(chosen if chosen in sizes else sizes[0])
            return await next_step()

        current = tile_span(draft.get(CONF_SPAN))
        default = current if current in sizes else sizes[0]
        hints = []
        if current != ONE and current not in sizes:
            hints.append(
                "The tile's current size does not fit at this position, so a smaller one is chosen."
            )
        if not tested:
            hints.append("Sizes above 1 × 1 are untested for this kind of tile.")
        if (cols, rows) not in sizes and taken and cols * rows > 1:
            hints.append("Full screen needs an empty screen.")
        schema = vol.Schema(
            {
                vol.Required("size", default=size_value(default)): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "value": size_value(s),
                                "label": size_label(s, cols, rows, experimental=not tested),
                            }
                            for s in sizes
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            description_placeholders={
                "tile": f"Screen {draft[CONF_SCREEN]}, position {draft[CONF_TILE]}",
                "hint": " ".join(hints),
            },
        )

    async def async_step_add_tile_size(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how many cells the new tile covers."""
        return await self._async_size_step(
            step_id="add_tile_size",
            user_input=user_input,
            next_step=self._async_after_add_size,
        )

    async def _async_after_add_size(self) -> ConfigFlowResult:
        if self._new_type == "playlists":
            return await self.async_step_add_tile_playlists()
        return await self.async_step_add_tile_background()

    async def async_step_edit_tile_size(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how many cells the edited tile covers."""
        return await self._async_size_step(
            step_id="edit_tile_size",
            user_input=user_input,
            next_step=self._async_after_edit_size,
            skip=self._edit_index,
        )

    async def _async_after_edit_size(self) -> ConfigFlowResult:
        if self._new_type == "playlists":
            return await self.async_step_edit_tile_playlists()
        return await self.async_step_edit_tile_background()

    async def async_step_edit_tile_playlists(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Playlists tiles only: change which playlists the tile lists."""
        assert self._new_tile_config is not None
        return await self._async_playlist_step(
            step_id="edit_tile_playlists",
            user_input=user_input,
            next_step=self.async_step_edit_tile_background,
            preselect=[p["uri"] for p in self._new_tile_config.get(CONF_PLAYLISTS, [])],
            keep_on_failure=True,
        )

    async def async_step_edit_tile_background(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the tile's background image, then save the edited tile."""
        try:
            assert self._new_tile_config is not None
            draft = self._new_tile_config

            if user_input is not None:
                name = user_input.get("background_image_name")
                if name and name != "none":
                    draft["background_image_name"] = name
                else:
                    draft.pop("background_image_name", None)
                self._tiles[self._edit_index] = draft
                _LOGGER.info(
                    f"Tile edited at screen {draft[CONF_SCREEN]}/position {draft[CONF_TILE]}"
                )
                new_options = dict(self._entry.options)
                new_options[CONF_TILES] = self._tiles
                return self.async_create_entry(title="", data=new_options)

            images = self._background_image_choices()
            image_options = [{"value": "none", "label": "No background image"}, *images]
            # An image deleted from the library while a tile still names it is no
            # longer an option, and a default outside the options fails the form's
            # own validation. Falling back to none also drops the dead reference.
            current = draft.get("background_image_name")
            default = current if current in {o["value"] for o in images} else "none"
            schema = vol.Schema(
                {
                    vol.Optional("background_image_name", default=default): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=image_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )
            return self.async_show_form(
                step_id="edit_tile_background",
                data_schema=schema,
                description_placeholders={
                    "tile": f"Screen {draft[CONF_SCREEN]}, Position {draft[CONF_TILE]}",
                    "images_available": f"{len(images)} image(s) available",
                },
            )
        except Exception as err:
            _LOGGER.error(f"Error in async_step_edit_tile_background: {err}", exc_info=True)
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
                "label": self._tile_choice_label(tile),
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

        # Bundled icons are marked because deleting one is sticky: it won't be
        # re-added on restart, unlike a user upload of the same name.
        options = [
            {
                "value": icon["id"],
                "label": (
                    f"{ICON_CATEGORIES.get(icon.get('category', ''), 'Other')}: {icon['name']}"
                    + (" (bundled)" if icon.get("bundled") else "")
                ),
            }
            for icon in sorted(
                icons,
                key=lambda i: (ICON_CATEGORIES.get(i.get("category", ""), "Other"), i["name"]),
            )
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
