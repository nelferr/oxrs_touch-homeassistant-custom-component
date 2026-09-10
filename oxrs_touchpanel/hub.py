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
    CONF_ACTION_ENTITY,
    CONF_ACTION_TILE_TYPE,
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
from .background_images import BackgroundImageManager

_LOGGER = logging.getLogger(__name__)


def _find_tile_type_for_domain(domain: str, tile_types: dict[str, Any]) -> str | None:
    """Find best matching tile type for an entity domain.
    
    Args:
        domain: Entity domain (e.g., "light", "cover")
        tile_types: TILE_TYPES dictionary
        
    Returns:
        Matching tile type name, or None if not found
    """
    _LOGGER.debug(f"_find_tile_type_for_domain({domain})")
    for tile_type_name, tile_def in tile_types.items():
        tile_domain = tile_def.get("domain", [])
        # Normalize domain to list for matching
        if isinstance(tile_domain, str):
            tile_domain = [tile_domain]
        _LOGGER.debug(f"  Checking {tile_type_name}: domain={tile_domain}")
        if domain in tile_domain:
            _LOGGER.debug(f"  ✓ Found match: {tile_type_name}")
            return tile_type_name
    _LOGGER.warning(f"  ✗ No matching tile type for domain: {domain}")
    return None


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
        # Initialize background image manager
        self.background_images = BackgroundImageManager(hass, entry.options)

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
        # Send background images for any tiles that have them
        await self.async_push_background_images()

    def _track_entities(self) -> None:
        """Track entity changes for tiles with entity bindings (old and new format)."""
        entity_ids = []
        
        # Old format: tiles with entity_id + type
        entity_ids.extend([
            t[CONF_ENTITY_ID]
            for t in self.tiles
            if t.get(CONF_ENTITY_ID) and (CONF_TYPE in t)
        ])
        
        # New format: flexible action tiles with action_entity
        entity_ids.extend([
            t[CONF_ACTION_ENTITY]
            for t in self.tiles
            if CONF_ACTIONS in t and t.get(CONF_ACTION_ENTITY)
        ])
        
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
                    # New format: flexible actions with optional entity binding
                    # Use the stored tile type if user chose one
                    matching_type = t.get(CONF_ACTION_TILE_TYPE)
                    
                    # Fallback to detecting from entity if not stored (backward compat)
                    if not matching_type and CONF_ACTION_ENTITY in t:
                        action_entity = t.get(CONF_ACTION_ENTITY)
                        entity_domain = action_entity.split(".")[0]
                        matching_type = _find_tile_type_for_domain(entity_domain, TILE_TYPES)
                    
                    if matching_type:
                        definition = TILE_TYPES[matching_type]
                        tile_conf: dict[str, Any] = {
                            "tile": t[CONF_TILE],
                            "style": definition["style"],
                            "label": t.get(CONF_LABEL) or "",
                            "icon": t.get(CONF_ICON) or definition["icon"],
                        }
                        config_extra = definition.get("config_extra")
                        if config_extra is not None:
                                # Pass the entity in tile config for config_extra to use
                                tile_conf.update(config_extra(self.hass, {**t, CONF_ENTITY_ID: action_entity}))
                        else:
                            # Fallback to button if no matching type
                            tile_conf = {
                                "tile": t[CONF_TILE],
                                "style": "button",
                                "label": t.get(CONF_LABEL) or "",
                                "icon": t.get(CONF_ICON) or "_onoff",
                            }
                    else:
                        # No entity binding - use generic button
                        tile_conf = {
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
            # Handle flexible action tiles with entity binding
            if CONF_ACTIONS in tile and tile.get(CONF_ACTIONS):
                action_entity = tile.get(CONF_ACTION_ENTITY)
                if action_entity:
                    # Use stored tile type if available, otherwise detect from entity
                    matching_type = tile.get(CONF_ACTION_TILE_TYPE)
                    if not matching_type:
                        entity_domain = action_entity.split(".")[0]
                        matching_type = _find_tile_type_for_domain(entity_domain, TILE_TYPES)
                    
                    if matching_type:
                        handler = TILE_TYPES.get(matching_type)
                        if handler:
                            # Create a temp tile config with the entity_id for build_state
                            temp_tile = {**tile, CONF_ENTITY_ID: action_entity}
                            state = handler["build_state"](self.hass, temp_tile)
                            if state is not None:
                                payload_tiles.append(state)
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

    async def async_push_background_images(self) -> None:
        """Send background images to panel for any tiles that have them.
        
        This is called after configuration is pushed to apply background images
        to tiles via OXRS cmnd/ topic with the image data.
        """
        _LOGGER.debug("Checking tiles for background images to send to panel...")
        
        for tile in self.tiles:
            background_image_id = tile.get("background_image_id")
            if not background_image_id:
                continue
            
            try:
                screen = tile[CONF_SCREEN]
                tile_num = tile[CONF_TILE]
                
                # Build OXRS MQTT payload with base64 image data
                payload = self.background_images.build_oxrs_tile_payload(
                    screen, tile_num, background_image_id
                )
                
                if payload:
                    _LOGGER.debug(
                        f"Sending background image {background_image_id} "
                        f"to screen {screen}, tile {tile_num}"
                    )
                    await mqtt.async_publish(
                        self.hass,
                        topic_cmnd(self.client_id),
                        json.dumps(payload),
                    )
                else:
                    _LOGGER.warning(
                        f"Failed to build OXRS payload for background image {background_image_id}"
                    )
            except Exception as err:
                _LOGGER.error(
                    f"Error sending background image for tile {tile_num}: {err}",
                    exc_info=True
                )

    @callback
    def _on_entity_change(self, event: Event) -> None:
        """Send updated state to panel when entity changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return
        entity_id = event.data["entity_id"]
        
        # Find tiles affected by this entity change
        tiles_to_update = []
        
        # OLD format: tiles with CONF_ENTITY_ID + CONF_TYPE
        old_format_tile = next(
            (t for t in self.tiles if t.get(CONF_ENTITY_ID) == entity_id and CONF_TYPE in t), None
        )
        if old_format_tile:
            tiles_to_update.append((old_format_tile, old_format_tile[CONF_TYPE]))
        
        # NEW format: flexible tiles with CONF_ACTION_ENTITY
        flexible_tiles = [
            t for t in self.tiles
            if CONF_ACTIONS in t and t.get(CONF_ACTIONS) and t.get(CONF_ACTION_ENTITY) == entity_id
        ]
        for flexible_tile in flexible_tiles:
            # Use stored tile type if available, otherwise detect from entity
            matching_type = flexible_tile.get(CONF_ACTION_TILE_TYPE)
            if not matching_type:
                entity_domain = entity_id.split(".")[0]
                matching_type = _find_tile_type_for_domain(entity_domain, TILE_TYPES)
            
            if matching_type:
                tiles_to_update.append((flexible_tile, matching_type))
        
        # Send cmnd/ updates for all affected tiles
        for tile, tile_type in tiles_to_update:
            handler = TILE_TYPES.get(tile_type)
            if handler is None:
                continue
            
            # For flexible tiles, we need to pass entity_id as CONF_ENTITY_ID for build_state
            temp_tile = tile
            if CONF_ACTION_ENTITY in tile and CONF_ENTITY_ID not in tile:
                temp_tile = {**tile, CONF_ENTITY_ID: entity_id}
            
            state = handler["build_state"](self.hass, temp_tile)
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
        
        _LOGGER.debug(f"_on_stat received: {json.dumps(payload)}")
        
        if not isinstance(payload, dict) or "type" not in payload:
            _LOGGER.debug(f"Invalid payload format (missing type): {payload}")
            return
        
        screen = payload.get("screen", 1)
        tile_idx = payload.get("tile", 1)
        
        _LOGGER.debug(f"Looking for tile: screen={screen}, tile={tile_idx}")
        _LOGGER.debug(f"Available tiles: {[(t.get(CONF_SCREEN), t.get(CONF_TILE)) for t in self.tiles]}")
        
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
        
        _LOGGER.debug(f"Found tile: {tile}")
        
        try:
            # Check for flexible tile format
            has_actions = CONF_ACTIONS in tile
            has_entity = CONF_ACTION_ENTITY in tile
            _LOGGER.debug(f"Tile format check - has {CONF_ACTIONS}: {has_actions}, has {CONF_ACTION_ENTITY}: {has_entity}")
            
            # NEW: Check if tile has flexible actions (new format) with entity binding
            if has_actions and tile.get(CONF_ACTIONS) and has_entity:
                _LOGGER.debug(f"Processing FLEXIBLE tile {screen}/{tile_idx}")
                action_entity = tile[CONF_ACTION_ENTITY]
                entity_domain = action_entity.split(".")[0]
                
                # Use stored tile type if available, otherwise detect from entity
                matching_type = tile.get(CONF_ACTION_TILE_TYPE)
                if not matching_type:
                    matching_type = _find_tile_type_for_domain(entity_domain, TILE_TYPES)
                
                _LOGGER.debug(f"Flexible tile bound to entity: {action_entity} → tile type: {matching_type}")
                
                if not matching_type:
                    _LOGGER.warning(f"No tile type found for domain {entity_domain} (entity {action_entity})")
                    _LOGGER.debug(f"Available tile types: {list(TILE_TYPES.keys())}")
                    return
                
                _LOGGER.debug(f"Matched domain {entity_domain} to tile type: {matching_type}")
                
                # Use the tile type's handle_event to process the MQTT payload
                # Build a temporary tile config that the handler expects
                temp_tile = {
                    **tile,
                    CONF_TYPE: matching_type,
                    CONF_ENTITY_ID: action_entity,
                }
                
                _LOGGER.debug(f"temp_tile config: {temp_tile}")
                
                handler = TILE_TYPES.get(matching_type)
                if handler is None:
                    _LOGGER.warning(f"Handler not found for tile type: {matching_type}")
                    return
                
                _LOGGER.info(f"Calling {matching_type} handle_event for flexible tile {screen}/{tile_idx}")
                _LOGGER.debug(f"Payload being passed to handler: {payload}")
                
                self.hass.async_create_task(
                    handler["handle_event"](self.hass, temp_tile, payload)
                )
                return
            
            # OLD: Handle with hardcoded tile type
            _LOGGER.debug(f"Processing HARDCODED tile {screen}/{tile_idx}")
            tile_type = tile.get(CONF_TYPE)
            if not tile_type:
                _LOGGER.warning(f"Tile {screen}/{tile_idx} has no type or actions")
                return
            
            handler = TILE_TYPES.get(tile_type)
            if handler is None:
                _LOGGER.warning(f"Unknown tile type: {tile_type}")
                return
            
            _LOGGER.debug(f"Calling {tile_type} handle_event for hardcoded tile {screen}/{tile_idx}")
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
