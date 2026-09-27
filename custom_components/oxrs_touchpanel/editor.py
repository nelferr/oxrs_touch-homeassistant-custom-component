"""The visual editor: a sidebar page that draws each panel's screens as the panel shows them.

The page (frontend/editor.js) is a plain web component served from this integration's
own folder. It gets everything it draws from one admin-only websocket command,
`oxrs_touchpanel/editor/panels`, which returns - for every loaded panel - the SAME
conf payload and tile states the hub publishes to the panel (hub.build_conf and
hub.build_tile_states), plus the library images and custom icons those reference.
Drawing from what is actually sent means the editor cannot drift from the panel.

This first version is read-only. Changing tiles still happens in the options dialog.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .boards import screen_size
from .const import (
    CONF_ACTION_ENTITY,
    CONF_ALBUM_ART,
    CONF_ENTITY_ID,
    CONF_LABEL,
    CONF_SCREEN,
    CONF_SPAN,
    CONF_TILE,
    CONF_TYPE,
    DOMAIN,
    LIBRARY_DATA_KEY,
)
from .grid import FOOTER_HEIGHT, TILE_PADDING, tile_span
from .tiles import TILE_TYPES

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"
STATIC_URL = f"/{DOMAIN}_static"
PANEL_URL_PATH = "oxrs-panels"
PANEL_ELEMENT = "oxrs-panel-editor"
WS_PANELS = f"{DOMAIN}/editor/panels"

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
        websocket_api.async_register_command(hass, ws_panels)
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


def panel_view(hass: HomeAssistant, entry_id: str, panel: Any) -> dict[str, Any]:
    """Everything the editor draws for one panel."""
    conf = panel.build_conf()
    states = panel.build_tile_states()
    cols, rows = panel.layout["horizontal"], panel.layout["vertical"]
    hardware = getattr(panel, "_reported_hardware", None) or panel.hardware
    width, height = screen_size(hardware)

    # Images and icons referenced by name, resolved to data URIs so the page needs
    # no extra requests. Built-in icons (names starting with "_") ship as files.
    library = hass.data.get(LIBRARY_DATA_KEY)
    images: dict[str, str] = {}
    icons: dict[str, str] = {}
    art: dict[str, str] = {}
    tiles_by_pos = {(t.get(CONF_SCREEN), t.get(CONF_TILE)): t for t in panel.tiles}

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
        if not isinstance(name, str) or not name:
            continue
        found = library.get_image_by_name(name) if library else None
        if found and name not in images:
            images[name] = _data_uri(found)
        elif not found:
            # Album art is generated for the panel and not kept; the page shows the
            # player's own cover from Home Assistant instead.
            source = tiles_by_pos.get((state.get("screen"), state.get("tile")))
            entity_id = source.get(CONF_ENTITY_ID) if source else None
            entity = hass.states.get(entity_id) if entity_id else None
            picture = entity.attributes.get("entity_picture") if entity else None
            if picture:
                art[f"{state.get('screen')}/{state.get('tile')}"] = picture

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
        "tiles": [_tile_meta(i, t) for i, t in enumerate(panel.tiles)],
        "images": images,
        "icons": icons,
        "art": art,
        "entities": sorted(
            {
                e
                for t in panel.tiles
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
    connection.send_result(msg["id"], {"panels": panels})
