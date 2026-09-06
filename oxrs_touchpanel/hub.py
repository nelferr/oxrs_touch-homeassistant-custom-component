"""The panel hub: MQTT plumbing + bidirectional tile<->entity bridge."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_ACTIONS,
    CONF_ENTITY_ID,
    CONF_ICON,
    CONF_LABEL,
    CONF_SCREEN,
    CONF_TILE,
    CONF_TILES,
    CONF_TYPE,
    DEFAULT_LAYOUT,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    signal_available,
    signal_tele,
    topic_cmnd,
    topic_conf,
    topic_lwt,
    topic_stat,
    topic_tele,
)
from .models import OxrsTile
from .tiles import TILE_TYPES

_LOGGER = logging.getLogger(__name__)


class OxrsPanel:
    """Represents a single OXRS Touch Panel (one MQTT client id)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the panel."""
        self.hass = hass
        self.entry = entry
        self.client_id: str = entry.data["client_id"]
        self.available: bool = False
        self.temperature: float | None = None
        self.humidity: float | None = None
        self.esp32_temp: float | None = None
        self._pushed_screens: set[int] = set()
        self._unsubs: list = []

    @property
    def tiles(self) -> list[dict[str, Any]]:
        """Configured tiles from the options flow."""
        return self.entry.options.get(CONF_TILES, [])

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry for this panel."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.client_id)},
            name=self.entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_setup(self) -> None:
        """Subscribe to panel topics and start tracking bound entities."""
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, topic_stat(self.client_id), self._on_stat
            )
        )
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, topic_lwt(self.client_id), self._on_lwt
            )
        )
        self._unsubs.append(
            await mqtt.async_subscribe(
                self.hass, topic_tele(self.client_id), self._on_tele
            )
        )
        self._track_entities()
        # Push config now in case the panel is already online.
        await self.async_push_config()

    def _track_entities(self) -> None:
        """Track entity changes for tiles with entity bindings (old format)."""
        # Only track tiles in old format (with entity_id + type)
        entity_ids = [
            t[CONF_ENTITY_ID]
            for t in self.tiles
            if t.get(CONF_ENTITY_ID) and (CONF_TYPE in t)
        ]
        if entity_ids:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, entity_ids, self._on_entity_change
                )
            )

    async def async_unload(self) -> None:
        """Tear down subscriptions and listeners."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    # ── outbound: HA -> panel ────────────────────────────────────────────────
    async def async_push_config(self) -> None:
        """Build and publish the screens config, then seed tile states."""
        screens: dict[int, list[dict[str, Any]]] = {}
        for tile in self.tiles:
            screens.setdefault(tile[CONF_SCREEN], []).append(tile)

        # Clean slate: remove every screen we manage (current + previously
        # pushed) so stale/duplicate tiles are dropped and the panel exactly
        # matches our config. Removing screen 1 recreates it empty.
        screen_ids = sorted(set(screens) | self._pushed_screens)
        if screen_ids:
            await mqtt.async_publish(
                self.hass,
                topic_cmnd(self.client_id),
                json.dumps(
                    {
                        "screens": [
                            {"screen": s, "action": "remove"} for s in screen_ids
                        ]
                    }
                ),
            )
        self._pushed_screens = set(screens)

        conf: dict[str, Any] = {"screens": []}
        for screen_idx, screen_tiles in sorted(screens.items()):
            tiles_conf: list[dict[str, Any]] = []
            for t in sorted(screen_tiles, key=lambda x: x[CONF_TILE]):
                # Support both old and new tile formats
                if CONF_ACTIONS in t and t.get(CONF_ACTIONS):
                    # New format: flexible actions
                    # Use generic "button" style for all flexible action tiles
                    tile_conf: dict[str, Any] = {
                        "tile": t[CONF_TILE],
                        "style": "button",
                        "label": t.get(CONF_LABEL) or "",
                        "icon": t.get(CONF_ICON) or "_onoff",
                    }
                else:
                    # Old format: hardcoded tile type
                    tile_type = t.get(CONF_TYPE)
                    definition = TILE_TYPES.get(tile_type)
                    if definition is None:
                        _LOGGER.warning(f"Unknown tile type: {tile_type}")
                        continue
                    
                    tile_conf = {
                        "tile": t[CONF_TILE],
                        "style": definition["style"],
                        "label": t.get(CONF_LABEL) or "",
                        "icon": t.get(CONF_ICON) or definition["icon"],
                    }
                    config_extra = definition.get("config_extra")
                    if config_extra is not None:
                        tile_conf.update(config_extra(self.hass, t))
                
                tiles_conf.append(tile_conf)
            
            conf["screens"].append(
                {
                    "screen": screen_idx,
                    "label": self.entry.title,
                    "screenLayout": DEFAULT_LAYOUT,
                    "tiles": tiles_conf,
                }
            )

        await mqtt.async_publish(
            self.hass, topic_conf(self.client_id), json.dumps(conf)
        )
        # Let the panel apply the config before seeding tile states.
        await asyncio.sleep(1)
        await self.async_seed_state()

    async def async_seed_state(self) -> None:
        """Publish current state for every configured tile in one message."""
        payload_tiles: list[dict[str, Any]] = []
        for tile in self.tiles:
            # Skip tiles with flexible actions - they don't have state
            if CONF_ACTIONS in tile and tile.get(CONF_ACTIONS):
                continue
            
            # Process old format tiles with entity bindings
            tile_type = tile.get(CONF_TYPE)
            handler = TILE_TYPES.get(tile_type)
            if handler is None:
                continue
            state = handler["build_state"](self.hass, tile)
            if state is not None:
                payload_tiles.append(state)
        
        if payload_tiles:
            await mqtt.async_publish(
                self.hass,
                topic_cmnd(self.client_id),
                json.dumps({"tiles": payload_tiles}),
            )

    @callback
    def _on_entity_change(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return
        entity_id = event.data["entity_id"]
        tile = next(
            (t for t in self.tiles if t.get(CONF_ENTITY_ID) == entity_id), None
        )
        if tile is None:
            return
        handler = TILE_TYPES.get(tile[CONF_TYPE])
        if handler is None:
            return
        state = handler["build_state"](self.hass, tile)
        if state is not None:
            self.hass.async_create_task(
                mqtt.async_publish(
                    self.hass,
                    topic_cmnd(self.client_id),
                    json.dumps({"tiles": [state]}),
                )
            )

    # ── inbound: panel -> HA ─────────────────────────────────────────────────
    @callback
    def _on_stat(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            _LOGGER.debug(f"Failed to parse MQTT payload: {msg.payload}")
            return
        
        if not isinstance(payload, dict) or "type" not in payload:
            _LOGGER.debug(f"Invalid payload format: {payload}")
            return
        
        screen = payload.get("screen", 1)
        tile_idx = payload.get("tile", 1)
        
        try:
            tile = next(
                (
                    t
                    for t in self.tiles
                    if t[CONF_SCREEN] == screen and t[CONF_TILE] == tile_idx
                ),
                None,
            )
        except Exception as err:
            _LOGGER.error(f"Error finding tile {screen}/{tile_idx}: {err}")
            return
        
        if tile is None:
            _LOGGER.debug(f"Tile not found: screen={screen}, tile={tile_idx}")
            return
        
        try:
            # NEW: Check if tile has flexible actions (new format)
            if CONF_ACTIONS in tile and tile.get(CONF_ACTIONS):
                _LOGGER.debug(f"Processing flexible actions for tile {screen}/{tile_idx}")
                try:
                    oxrs_tile = OxrsTile(self.hass, tile)
                    for idx, action in enumerate(oxrs_tile.actions):
                        _LOGGER.debug(f"Running action {idx} for tile {screen}/{tile_idx}")
                        self.hass.async_create_task(
                            action.run(
                                data={
                                    "payload": payload,
                                    "tile_id": f"{screen}_{tile_idx}",
                                }
                            )
                        )
                except Exception as err:
                    _LOGGER.error(
                        f"Error processing flexible actions for {screen}/{tile_idx}: {err}",
                        exc_info=True,
                    )
                return
            
            # OLD: Handle with hardcoded tile type
            tile_type = tile.get(CONF_TYPE)
            if not tile_type:
                _LOGGER.warning(f"Tile {screen}/{tile_idx} has no type or actions")
                return
            
            handler = TILE_TYPES.get(tile_type)
            if handler is None:
                _LOGGER.warning(f"Unknown tile type: {tile_type}")
                return
            
            _LOGGER.debug(f"Processing {tile_type} tile {screen}/{tile_idx}")
            self.hass.async_create_task(
                handler["handle_event"](self.hass, tile, payload)
            )
        except Exception as err:
            _LOGGER.error(
                f"Unexpected error in _on_stat for {screen}/{tile_idx}: {err}",
                exc_info=True,
            )

    @callback
    def _on_lwt(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            online = bool(json.loads(msg.payload).get("online", False))
        except (ValueError, TypeError, AttributeError):
            online = str(msg.payload).strip().lower() in ("online", "1", "true")
        was_available = self.available
        self.available = online
        async_dispatcher_send(self.hass, signal_available(self.client_id))
        # (Re)configure whenever the panel (re)connects.
        if online and not was_available:
            self.hass.async_create_task(self.async_push_config())

    @callback
    def _on_tele(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        if "temperature" in data:
            self.temperature = data["temperature"]
        if "humidity" in data:
            self.humidity = data["humidity"]
        if "esp32Temp" in data:
            self.esp32_temp = data["esp32Temp"]
        async_dispatcher_send(self.hass, signal_tele(self.client_id))
