"""The visual editor: a sidebar page that draws each panel's screens as the panel shows them.

The page (frontend/editor.js) is a plain web component served from this integration's
own folder. It gets everything it draws from admin-only websocket commands:
`oxrs_touchpanel/editor/panels`, and `.../preview` for a draft of unsaved changes, which
return the SAME conf payload and tile states the hub publishes to the panel
(hub.build_conf and hub.build_tile_states), plus the library images and custom icons
those reference.
Drawing from what is actually sent means the editor cannot drift from the panel.

Editing: a tile's form is built by the options dialog's own code (the same fields,
entity filtering and icon lists) and sent to the page as Home Assistant's serialized
form, which the page renders with HA's own form element. Submitting it goes through
the dialog's own input handling, so a tile made here is the tile the dialog would
make. Edits are staged in the page and saved with one "Apply to panel", which the
server checks again (drafts.py) and refuses if the tiles changed in the dialog since
the page loaded them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .boards import screen_size
from .const import (
    CONF_ACTION_ENTITY,
    CONF_ALBUM_ART,
    CONF_ENTITY_ID,
    CONF_ICON,
    CONF_LABEL,
    CONF_PLAYLISTS,
    CONF_SCREEN,
    CONF_SCREEN_COLORS,
    CONF_SCREEN_NAMES,
    CONF_SPAN,
    CONF_TILE,
    CONF_TILES,
    CONF_TYPE,
    DOMAIN,
    FIELD_LARGER,
    LIBRARY_DATA_KEY,
    MAX_PLAYLISTS,
)
from .drafts import clean_screens, draft_problems, layout_fingerprint, screens_problems
from .grid import (
    FOOTER_HEIGHT,
    ONE,
    TILE_PADDING,
    fitting_sizes,
    is_size_tested,
    occupied,
    parse_size_value,
    size_label,
    size_value,
    tile_span,
)
from .tiles import TILE_TYPES

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"
STATIC_URL = f"/{DOMAIN}_static"
PANEL_URL_PATH = "oxrs-panels"
PANEL_ELEMENT = "oxrs-panel-editor"
WS_PANELS = f"{DOMAIN}/editor/panels"
WS_PREVIEW = f"{DOMAIN}/editor/preview"
WS_TILE_FORM = f"{DOMAIN}/editor/tile_form"
WS_BUILD_TILE = f"{DOMAIN}/editor/build_tile"
WS_APPLY = f"{DOMAIN}/editor/apply"

# The background image field, as the dialog's background step names it.
BACKGROUND_FIELD = "background_image_name"
NO_IMAGE = "none"
_LABELS = f"{DOMAIN}_editor_labels"

_REGISTERED = f"{DOMAIN}_editor_registered"
_PANEL_SHOWN = f"{DOMAIN}_editor_panel_shown"


async def async_setup_editor(hass: HomeAssistant, version: str) -> None:
    """Serve the page, register its websocket command and show it in the sidebar.

    The static path and the command are registered once per Home Assistant run
    (neither can be unregistered); the sidebar entry is added with the first panel
    and removed when the last one is deleted (async_remove_editor_panel).
    """
    if not hass.data.get(_REGISTERED):
        await hass.http.async_register_static_paths(
            # No cache headers: the module URL carries the version instead, so an
            # update is picked up while an unchanged page can still be cached.
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        for handler in (ws_panels, ws_preview, ws_tile_form, ws_build_tile, ws_apply):
            websocket_api.async_register_command(hass, handler)
        hass.data[_REGISTERED] = True

    if not hass.data.get(_PANEL_SHOWN):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_ELEMENT,
            sidebar_title="OXRS panels",
            sidebar_icon="mdi:tablet-dashboard",
            module_url=f"{STATIC_URL}/editor.js?v={version}",
            require_admin=True,
            config={"static_url": STATIC_URL},
        )
        hass.data[_PANEL_SHOWN] = True


@callback
def async_remove_editor_panel(hass: HomeAssistant) -> None:
    """Take the page out of the sidebar (the last panel has been deleted)."""
    if hass.data.pop(_PANEL_SHOWN, False):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)


def _tile_meta(index: int, tile: dict[str, Any]) -> dict[str, Any]:
    """What the side sheet says about a tile, from its stored config."""
    definition = TILE_TYPES.get(tile.get(CONF_TYPE))
    w, h = tile_span(tile.get(CONF_SPAN))
    return {
        "index": index,
        "screen": tile.get(CONF_SCREEN),
        "tile": tile.get(CONF_TILE),
        "type": tile.get(CONF_TYPE),
        "type_label": definition["label"] if definition else "Action tile",
        "entity_id": tile.get(CONF_ENTITY_ID) or tile.get(CONF_ACTION_ENTITY),
        "label": tile.get(CONF_LABEL) or "",
        "size": [w, h],
        "album_art": bool(tile.get(CONF_ALBUM_ART)),
    }


def _data_uri(item: dict[str, Any], fallback_format: str = "png") -> str:
    fmt = str(item.get("format") or fallback_format).lower()
    return f"data:image/{fmt};base64,{item['data']}"


def _stored_screens(panel: Any) -> tuple[dict[str, str], dict[str, Any]]:
    names = panel.entry.options.get(CONF_SCREEN_NAMES) or {}
    colours = panel.entry.options.get(CONF_SCREEN_COLORS) or {}
    return (
        dict(names) if isinstance(names, dict) else {},
        dict(colours) if isinstance(colours, dict) else {},
    )


def panel_view(
    hass: HomeAssistant,
    entry_id: str,
    panel: Any,
    tiles: list[dict[str, Any]] | None = None,
    screen_names: dict[str, str] | None = None,
    screen_colors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the editor draws for one panel.

    tiles, screen_names and screen_colors, when given, are an unsaved draft: it is
    drawn through the same hub code as the stored config, so the preview is what
    applying it would send.
    """
    stored = panel.tiles
    stored_names, stored_colours = _stored_screens(panel)
    tiles = stored if tiles is None else tiles
    names = stored_names if screen_names is None else screen_names
    colours = stored_colours if screen_colors is None else screen_colors
    conf = panel.build_conf(tiles, screen_names=names, screen_colors=colours)
    states = panel.build_tile_states(tiles)
    cols, rows = panel.layout["horizontal"], panel.layout["vertical"]
    hardware = getattr(panel, "_reported_hardware", None) or panel.hardware
    width, height = screen_size(hardware)

    # Images and icons referenced by name, resolved to data URIs so the page needs
    # no extra requests. Built-in icons (names starting with "_") ship as files.
    library = hass.data.get(LIBRARY_DATA_KEY)
    images: dict[str, str] = {}
    icons: dict[str, str] = {}

    def want_icon(name: Any) -> None:
        if not isinstance(name, str) or not name or name.startswith("_") or name in icons:
            return
        found = library.get_icon_by_name(name) if library else None
        if found:
            icons[name] = _data_uri(found)

    for screen in conf.get("screens", []):
        for tile in screen.get("tiles", []):
            want_icon(tile.get("icon"))
    for state in states:
        want_icon(state.get("icon"))
        image = state.get("backgroundImage")
        name = image.get("name") if isinstance(image, dict) else None
        found = library.get_image_by_name(name) if library and isinstance(name, str) and name else None
        if found and name not in images:
            images[name] = _data_uri(found)

    # Album art is generated for the panel and not kept, so the page shows the
    # player's own cover. Taken from the tile's setting rather than the state sent,
    # so a tile just given album art in a draft already shows it.
    art: dict[str, str] = {}
    for tile in tiles:
        if not tile.get(CONF_ALBUM_ART):
            continue
        entity = hass.states.get(tile.get(CONF_ENTITY_ID) or "")
        picture = entity.attributes.get("entity_picture") if entity else None
        if picture:
            art[f"{tile.get(CONF_SCREEN)}/{tile.get(CONF_TILE)}"] = picture

    return {
        "entry_id": entry_id,
        "title": panel.entry.title,
        "client_id": panel.client_id,
        "available": bool(getattr(panel, "available", False)),
        "hardware": hardware,
        "grid": {"cols": cols, "rows": rows},
        "screen_px": {"width": width, "height": height},
        "footer_px": FOOTER_HEIGHT,
        "padding_px": TILE_PADDING,
        "conf": conf,
        "states": states,
        "tiles": [_tile_meta(i, t) for i, t in enumerate(tiles)],
        # The stored tiles, which a draft starts from, and their fingerprint, which
        # an apply checks so it cannot overwrite a change made in the dialog.
        "config_tiles": stored,
        "config_screen_names": stored_names,
        "config_screen_colors": stored_colours,
        "fingerprint": layout_fingerprint(stored, stored_names, stored_colours),
        # The screens' own names and colours as drawn (the conf's label falls back to
        # the panel title, so the page needs the raw values to edit them).
        "screen_names": names,
        "screen_colors": colours,
        "images": images,
        "icons": icons,
        "art": art,
        "entities": sorted(
            {
                e
                for t in tiles
                for e in (t.get(CONF_ENTITY_ID), t.get(CONF_ACTION_ENTITY))
                if isinstance(e, str) and e
            }
        ),
    }


@websocket_api.websocket_command({vol.Required("type"): WS_PANELS})
@websocket_api.require_admin
@callback
def ws_panels(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Every loaded panel, as the editor draws it."""
    panels = []
    for entry_id, panel in sorted(
        (hass.data.get(DOMAIN) or {}).items(), key=lambda item: item[1].entry.title.lower()
    ):
        try:
            panels.append(panel_view(hass, entry_id, panel))
        except Exception:  # noqa: BLE001 - one broken panel must not blank the page
            _LOGGER.exception("Could not build the editor view of %s", panel.entry.title)
    connection.send_result(msg["id"], {"panels": panels, "types": tile_types(hass)})


# ── editing ───────────────────────────────────────────────────────────────


def tile_types(hass: HomeAssistant) -> list[dict[str, Any]]:
    """The tile types a new tile can be, as the dialog offers them.

    A type tied to an integration (Music Assistant playlists) is only offered while
    that integration is loaded, or its entity picker would be empty.
    """
    types = []
    for key, definition in TILE_TYPES.items():
        integration = definition.get("integration")
        if integration and not any(
            entry.state is ConfigEntryState.LOADED
            for entry in hass.config_entries.async_entries(integration)
        ):
            continue
        types.append(
            {
                "type": key,
                "label": definition["label"],
                "icon": definition["icon"],
                "style": definition["style"],
                "big_tested": is_size_tested(definition["style"]),
            }
        )
    return types


def _panel(hass: HomeAssistant, entry_id: str) -> Any:
    return (hass.data.get(DOMAIN) or {}).get(entry_id)


def _flow(hass: HomeAssistant, panel: Any, tiles: list[dict[str, Any]]) -> Any:
    """The options dialog's flow for this panel, working on a draft's tiles.

    Used for its form builder and input handling, never shown. Imported here rather
    than at the top because the config flow pulls in a good deal of Home Assistant.
    """
    from .config_flow import OxrsOptionsFlow

    flow = OxrsOptionsFlow(panel.entry)
    flow.hass = hass
    flow._tiles = [dict(t) for t in tiles]
    return flow


class _Target:
    """The tile a form is for: a new one at a screen position, or one in the draft."""

    def __init__(self, tiles: list[dict[str, Any]], msg: dict[str, Any]) -> None:
        index = msg.get("index")
        self.index: int | None = None
        self.current: dict[str, Any] | None = None
        self.error: str | None = None
        if index is not None:
            if not 0 <= index < len(tiles) or tiles[index].get(CONF_TYPE) not in TILE_TYPES:
                self.error = "That tile cannot be edited here."
                return
            self.index = index
            self.current = tiles[index]
            self.type = self.current[CONF_TYPE]
            self.screen = self.current.get(CONF_SCREEN)
            self.position = self.current.get(CONF_TILE)
        else:
            self.type = msg.get("type")
            self.screen = msg.get("screen")
            self.position = msg.get("position")
            if self.type not in TILE_TYPES:
                self.error = "Unknown tile type."


def _sizes(panel: Any, tiles: list[dict[str, Any]], target: _Target) -> list[dict[str, Any]] | None:
    """The sizes that fit at the target's position, or None if its cell is taken."""
    cols, rows = panel.layout["horizontal"], panel.layout["vertical"]
    position = target.position
    if not isinstance(position, int) or not 1 <= position <= cols * rows:
        return None
    taken = occupied(tiles, target.screen, cols, rows, skip=target.index)
    if position in taken:
        return None
    tested = is_size_tested(TILE_TYPES[target.type]["style"])
    return [
        {
            "value": size_value(s),
            "label": size_label(s, cols, rows, experimental=not tested and s != ONE),
        }
        for s in fitting_sizes(position, taken, cols, rows)
    ]


def _form_schema(flow: Any, target: _Target) -> vol.Schema:
    """The dialog's details form, less the position and "larger" fields (the page
    places and sizes tiles itself), plus the dialog's background image choice."""
    full = flow._tile_details_schema(target.type, [target.position], current=target.current)
    fields = {k: v for k, v in full.schema.items() if str(k) not in (CONF_TILE, FIELD_LARGER)}
    images = flow._background_image_choices()
    current = (target.current or {}).get(BACKGROUND_FIELD)
    default = current if current in {o["value"] for o in images} else NO_IMAGE
    fields[vol.Optional(BACKGROUND_FIELD, default=default)] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[{"value": NO_IMAGE, "label": "No background image"}, *images],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    return vol.Schema(fields)


async def _labels(hass: HomeAssistant) -> dict[str, Any]:
    """The dialog's field labels and descriptions, from its own translations."""
    if _LABELS not in hass.data:
        path = Path(__file__).parent / "translations" / "en.json"
        text = await hass.async_add_executor_job(path.read_text, "utf-8")
        steps = json.loads(text)["options"]["step"]
        out: dict[str, Any] = {}
        for mode in ("add", "edit"):
            details = steps.get(f"{mode}_tile_details", {})
            out[mode] = {
                "labels": {
                    **details.get("data", {}),
                    BACKGROUND_FIELD: steps.get(f"{mode}_tile_background", {}).get("data", {}).get(BACKGROUND_FIELD, "Background image"),
                    CONF_PLAYLISTS: steps.get(f"{mode}_tile_playlists", {}).get("data", {}).get(CONF_PLAYLISTS, "Playlists"),
                },
                "descriptions": details.get("data_description", {}),
            }
        hass.data[_LABELS] = out
    return hass.data[_LABELS]


def serialize_schema(schema: vol.Schema) -> list[dict[str, Any]]:
    """A form schema in the shape Home Assistant's <ha-form> takes.

    What Home Assistant's flows do with a serializer library, done here because that
    library has changed: voluptuous_serialize is gone from 2026.9 (replaced by
    probatio), and importing it stopped the integration loading. Only what the tile
    form uses is handled: keys that are vol.Required / vol.Optional, values that are
    selectors - which describe themselves for the frontend with Selector.serialize().
    The fields match HA's own: name, required / optional, default, description.
    """
    fields: list[dict[str, Any]] = []
    for key, value in schema.schema.items():
        field: dict[str, Any] = {"name": str(key.schema)}
        field["required" if isinstance(key, vol.Required) else "optional"] = True
        # A marker without a default holds an "undefined" sentinel, which is not
        # callable; which object that is differs between voluptuous and probatio.
        default = getattr(key, "default", None)
        if callable(default) and type(default).__name__ != "Undefined":
            field["default"] = default()
        if getattr(key, "description", None):
            field["description"] = key.description
        if not isinstance(value, selector.Selector):
            raise TypeError(f"field {field['name']} is not a selector")
        field.update(value.serialize())
        fields.append(field)
    return fields


def _errors(err: vol.Invalid) -> dict[str, str]:
    errors = getattr(err, "errors", None) or [err]
    return {
        (str(e.path[0]) if e.path else "base"): e.msg
        for e in errors
    }


_DRAFT = {
    vol.Required("entry_id"): str,
    vol.Required("tiles"): [dict],
    # Screen names and colours, as stored ({"<screen>": ...}); absent means unchanged.
    vol.Optional("screen_names"): dict,
    vol.Optional("screen_colors"): dict,
}


@websocket_api.websocket_command({vol.Required("type"): WS_PREVIEW, **_DRAFT})
@websocket_api.require_admin
@callback
def ws_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """A panel drawn with a draft's tiles and screens instead of the stored ones."""
    panel = _panel(hass, msg["entry_id"])
    if panel is None:
        connection.send_error(msg["id"], "not_found", "That panel is not set up and running.")
        return
    try:
        view = panel_view(
            hass, msg["entry_id"], panel, msg["tiles"], msg.get("screen_names"), msg.get("screen_colors")
        )
    except Exception as err:  # noqa: BLE001 - a bad draft must not break the page
        _LOGGER.exception("Could not preview a draft for %s", panel.entry.title)
        connection.send_error(msg["id"], "preview_failed", f"Couldn't draw these changes: {err}")
        return
    connection.send_result(msg["id"], view)


# "type" is the command's own name, so the tile type travels as "tile_type".
_TARGET = {
    vol.Optional("index"): vol.Any(None, int),
    vol.Optional("screen"): int,
    vol.Optional("position"): int,
}


@websocket_api.websocket_command({vol.Required("type"): WS_TILE_FORM, **_DRAFT, **_TARGET, vol.Optional("tile_type"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_tile_form(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The form for adding a tile of a type at a position, or for editing a draft tile."""
    panel = _panel(hass, msg["entry_id"])
    if panel is None:
        connection.send_error(msg["id"], "not_found", "That panel is not set up and running.")
        return
    tiles = msg["tiles"]
    target = _Target(tiles, {**msg, "type": msg.get("tile_type")})
    if target.error:
        connection.send_error(msg["id"], "invalid", target.error)
        return
    sizes = _sizes(panel, tiles, target)
    if sizes is None:
        connection.send_error(msg["id"], "position_taken", "That position is not free.")
        return
    flow = _flow(hass, panel, tiles)
    schema = _form_schema(flow, target)
    labels = await _labels(hass)
    current = tile_span((target.current or {}).get(CONF_SPAN))
    connection.send_result(
        msg["id"],
        {
            "tile_type": target.type,
            "type_label": TILE_TYPES[target.type]["label"],
            "screen": target.screen,
            "position": target.position,
            "schema": serialize_schema(schema),
            "sizes": sizes,
            "size": size_value(current if size_value(current) in {s["value"] for s in sizes} else ONE),
            "playlists": target.type == "playlists",
            **labels["edit" if target.index is not None else "add"],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_BUILD_TILE,
        **_DRAFT,
        **_TARGET,
        vol.Optional("tile_type"): str,
        vol.Required("size"): str,
        vol.Required("input"): dict,
        vol.Optional("playlists"): vol.Any(None, [str]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_build_tile(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Turn a submitted tile form into the tile the dialog would have saved.

    Replies {"tile": ...}, or {"errors": {field: message}} for the form to show, or
    - for a playlists tile - {"playlists": [...]} asking which to list.
    """
    panel = _panel(hass, msg["entry_id"])
    if panel is None:
        connection.send_error(msg["id"], "not_found", "That panel is not set up and running.")
        return
    tiles = msg["tiles"]
    target = _Target(tiles, {**msg, "type": msg.get("tile_type")})
    if target.error:
        connection.send_error(msg["id"], "invalid", target.error)
        return
    sizes = _sizes(panel, tiles, target)
    if sizes is None:
        connection.send_error(msg["id"], "position_taken", "That position is not free.")
        return
    flow = _flow(hass, panel, tiles)
    try:
        data = _form_schema(flow, target)(msg["input"])
    except vol.Invalid as err:
        connection.send_result(msg["id"], {"errors": _errors(err)})
        return

    size = parse_size_value(msg["size"]) or ONE
    if size_value(size) not in {s["value"] for s in sizes}:
        connection.send_result(msg["id"], {"errors": {"size": "That size does not fit here."}})
        return

    definition = TILE_TYPES[target.type]
    # Editing starts from the tile as it is, so anything the form does not show
    # (its playlists, for one) is kept. The screen, position and type stay.
    tile: dict[str, Any] = (
        dict(target.current)
        if target.current is not None
        else {CONF_SCREEN: target.screen, CONF_TILE: target.position, CONF_TYPE: target.type}
    )
    tile[CONF_ENTITY_ID] = data[CONF_ENTITY_ID]
    tile[CONF_LABEL] = data.get(CONF_LABEL, "")
    tile[CONF_ICON] = data.get(CONF_ICON, definition["icon"])
    flow._apply_details_input(tile, target.type, data)
    if size != ONE:
        tile[CONF_SPAN] = list(size)
    else:
        tile.pop(CONF_SPAN, None)
    image = data.get(BACKGROUND_FIELD)
    if image and image != NO_IMAGE:
        # The dropdown only offers library images; a name from anywhere else is refused
        # here too, not left to the selector alone.
        if image not in {o["value"] for o in flow._background_image_choices()}:
            connection.send_result(msg["id"], {"errors": {BACKGROUND_FIELD: "That image is not in the library."}})
            return
        tile[BACKGROUND_FIELD] = image
    else:
        tile.pop(BACKGROUND_FIELD, None)

    if target.type == "playlists":
        choices = await flow._async_fetch_playlists(tile[CONF_ENTITY_ID])
        kept = target.current.get(CONF_PLAYLISTS) if target.current else None
        if not choices:
            # As in the dialog: an edit keeps the playlists it has when Music
            # Assistant cannot list them; a new tile has nothing to fall back on.
            if kept:
                connection.send_result(msg["id"], {"tile": tile})
                return
            reason = (
                "Music Assistant couldn't be reached to list its playlists."
                if choices is None
                else "That player's Music Assistant library has no playlists."
            )
            connection.send_result(msg["id"], {"errors": {"base": reason}})
            return
        chosen = msg.get("playlists")
        if chosen is None:
            known = {p["uri"] for p in choices}
            connection.send_result(
                msg["id"],
                {
                    "playlists": [{"value": p["uri"], "label": p["name"]} for p in choices],
                    "preselect": [p["uri"] for p in (kept or []) if p.get("uri") in known],
                    "max": MAX_PLAYLISTS,
                },
            )
            return
        by_uri = {p["uri"]: p for p in choices}
        picked = [by_uri[uri] for uri in chosen if uri in by_uri]
        if not picked:
            connection.send_result(msg["id"], {"errors": {"playlists": "Choose at least one playlist."}})
            return
        if len(picked) > MAX_PLAYLISTS:
            connection.send_result(msg["id"], {"errors": {"playlists": f"Choose at most {MAX_PLAYLISTS} playlists."}})
            return
        tile[CONF_PLAYLISTS] = picked

    connection.send_result(msg["id"], {"tile": tile})


@websocket_api.websocket_command(
    {vol.Required("type"): WS_APPLY, **_DRAFT, vol.Required("fingerprint"): str}
)
@websocket_api.require_admin
@callback
def ws_apply(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Save a draft's tiles and screens. The entry reloads and the panel is sent them once."""
    panel = _panel(hass, msg["entry_id"])
    if panel is None:
        connection.send_error(msg["id"], "not_found", "That panel is not set up and running.")
        return
    stored_names, stored_colours = _stored_screens(panel)
    if layout_fingerprint(panel.tiles, stored_names, stored_colours) != msg["fingerprint"]:
        connection.send_error(
            msg["id"],
            "changed_elsewhere",
            "This panel's tiles or screens were changed somewhere else since this page loaded "
            "them. Discard these changes and make them again.",
        )
        return
    names = msg.get("screen_names", stored_names)
    colours = msg.get("screen_colors", stored_colours)
    cols, rows = panel.layout["horizontal"], panel.layout["vertical"]
    problems = draft_problems(panel.tiles, msg["tiles"], cols, rows) + screens_problems(names, colours, stored_names, stored_colours)
    if problems:
        connection.send_error(msg["id"], "invalid", " ".join(problems))
        return
    names, colours = clean_screens(names, colours)
    options = dict(panel.entry.options)
    options[CONF_TILES] = msg["tiles"]
    options[CONF_SCREEN_NAMES] = names
    options[CONF_SCREEN_COLORS] = colours
    hass.config_entries.async_update_entry(panel.entry, options=options)
    connection.send_result(msg["id"], {"ok": True})
