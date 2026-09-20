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

from .albumart import (
    art_image_name,
    art_revision,
    art_source_url,
    async_build_art_payload,
)
from .colors import BLACK, normalize_rgb, override_payload, rgb_payload
from .const import (
    CONF_ACTION_ENTITY,
    CONF_ACTION_TILE_TYPE,
    CONF_ACTIONS,
    CONF_ALBUM_ART,
    CONF_ALBUM_ART_BUDGET,
    CONF_BACKGROUND_COLOR,
    CONF_ENTITY_ID,
    CONF_ICON,
    CONF_ICON_ON_COLOR,
    CONF_INDICATOR_SECONDARY_ENTITY_ID,
    CONF_LABEL,
    CONF_PANEL_SETTINGS,
    CONF_SCREEN,
    CONF_SCREEN_COLORS,
    CONF_SCREEN_NAMES,
    CONF_SUBLABEL_ENTITY_ID,
    CONF_TILE,
    CONF_TILES,
    CONF_TYPE,
    DEFAULT_ALBUM_ART_BUDGET,
    DEFAULT_BACKGROUND_COLOR,
    DEFAULT_ICON_ON_COLOR,
    DEFAULT_LAYOUT,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    PANEL_SETTINGS,
    signal_available,
    signal_tele,
    topic_cmnd,
    topic_conf,
    topic_lwt,
    topic_stat,
    topic_tele,
)
from .models import OxrsTile
from .library import SharedMediaLibrary
from .tiles import TILE_TYPES, TileType

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


def _augment_tile_state(
    hass: HomeAssistant,
    state: dict[str, Any],
    tile: dict[str, Any],
    library: SharedMediaLibrary,
    album_art_names: set[str] | None = None,
) -> None:
    """Apply common cross-tile-type extras to a cmnd state payload.

    Currently handles four capabilities that apply to ANY tile type:

    1. Background image (OXRS two-step process, step 2): after addImage has
       been sent, tile payloads reference the image by name.

       Firmware note (verified against classTile.cpp::setIconText): sending
       "text": "" does NOT hide the icon - it restores it. An empty string
       reverts to the icon; only a non-empty string hides the icon and shows
       text instead. We use a single space so the icon is hidden and no
       visible label is drawn over the background image.

    2. subLabel sourced from a secondary entity the user picked when
       creating the tile (e.g. "21.4°C", "on" / "off", a last-changed
       sensor). Rendered as "<state> <unit>", trimmed.

    3. State icon: a tile whose icon is half of a bundled pair (door-closed /
       door-open) shows the half matching its on/off state. Only tiles that
       report a "state" can swap. The other half reaches the panel alongside
       the configured icon, in async_push_images_to_panel.

    4. Album art: a media tile with CONF_ALBUM_ART shows the player's current
       artwork as its background, under a fixed per-player image name. This
       runs AFTER the static background above and wins if a tile somehow has
       both, since the art is the more specific choice.

       Because showing any background image requires non-empty text (see note
       1), the tile's icon is hidden while art is up - that is the accepted
       cost of putting art on the transport tile rather than a tile of its
       own. When the player has no art, the background is cleared explicitly
       and the icon restored: the panel keeps whatever it was last given, so
       omitting the field would leave a stale cover on screen.

    Args:
        hass:    Home Assistant instance (needed to read the subLabel entity)
        state:   Tile state payload dict (modified in-place)
        tile:    Tile config dict (may contain background_image_name /
                 sublabel_entity_id / icon / album_art)
        library: Shared media library, to confirm both halves of a pair exist
        album_art_names: Art image names currently loaded on this panel. None
                 means "unknown", which suppresses art rather than referencing
                 an image the panel may not hold.
    """
    image_name = tile.get("background_image_name")
    # Hiding the icon only makes sense if there is an image to show instead. A
    # tile can outlive its image (the library lets one be deleted while a tile
    # still names it), and referencing a name the panel was never sent leaves a
    # blank tile with no icon - white when lit.
    if image_name and library.get_image_by_name(image_name) is not None:
        state["backgroundImage"] = {"name": image_name}
        state["text"] = " "   # non-empty text hides the icon (see note above)
        _LOGGER.debug(
            f"Injected backgroundImage '{image_name}' + hid icon "
            f"for S{state.get('screen')}/T{state.get('tile')}"
        )

    sublabel_entity_id = tile.get(CONF_SUBLABEL_ENTITY_ID)
    if sublabel_entity_id:
        sublabel_state = hass.states.get(sublabel_entity_id)
        if sublabel_state is not None:
            unit = sublabel_state.attributes.get("unit_of_measurement") or ""
            sub_label = f"{sublabel_state.state} {unit}".strip()
            state["subLabel"] = sub_label
            _LOGGER.debug(
                f"Injected subLabel '{sub_label}' from {sublabel_entity_id} "
                f"for S{state.get('screen')}/T{state.get('tile')}"
            )

    icon = tile.get(CONF_ICON)
    if icon and "state" in state:
        swapped = library.state_icon(icon, state["state"] == "on")
        if swapped is not None:
            state["icon"] = swapped

    if tile.get(CONF_ALBUM_ART):
        art_entity_id = tile.get(CONF_ENTITY_ID) or tile.get(CONF_ACTION_ENTITY)
        art_name = art_image_name(art_entity_id) if art_entity_id else None
        if art_name and art_name in (album_art_names or set()):
            state["backgroundImage"] = {"name": art_name}
            state["text"] = " "
            # The icon is hidden behind the art anyway; dropping it keeps the
            # payload honest about what the panel will actually draw.
            state.pop("icon", None)
            _LOGGER.debug(
                f"Injected album art '{art_name}' "
                f"for S{state.get('screen')}/T{state.get('tile')}"
            )
        else:
            state["backgroundImage"] = {}
            state["text"] = ""   # empty text restores the icon (see note above)


class OxrsPanel:
    """Represents a single OXRS Touch Panel (one MQTT client id)."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, library: SharedMediaLibrary
    ) -> None:
        """Initialise the panel.
        
        Args:
            library: The shared media library (images/icons), reused across
                     every configured panel - see library.py.
        """
        self.hass = hass
        self.entry = entry
        self.client_id: str = entry.data["client_id"]
        self.available: bool = False
        self.temperature: float | None = None
        self.humidity: float | None = None
        self.esp32_temp: float | None = None
        self._pushed_screens: set[int] = set()
        self._unsubs: list = []
        self.library = library
        # Album art image name -> the revision currently on the panel. Both
        # reset when the panel reconnects, since it keeps no images across a
        # restart. Membership doubles as "this name is safe to reference".
        self._album_art: dict[str, str] = {}

    @property
    def tiles(self) -> list[dict[str, Any]]:
        """Configured tiles from the options flow."""
        return self.entry.options.get(CONF_TILES, [])

    @property
    def album_art_tiles(self) -> list[dict[str, Any]]:
        """Tiles configured to show the player's current artwork."""
        return [t for t in self.tiles if t.get(CONF_ALBUM_ART)]

    @property
    def album_art_budget(self) -> int:
        """Largest addImage payload album art may produce on this panel."""
        return int(
            self.entry.options.get(CONF_ALBUM_ART_BUDGET, DEFAULT_ALBUM_ART_BUDGET)
        )

    @property
    def panel_settings(self) -> dict[str, int]:
        """Display settings to push in conf/, one entry per firmware key.

        Stored values are laid over the firmware defaults and clamped to the
        firmware's limits, so an option saved by an older version, or edited by
        hand, can never send the panel something it would reject.
        """
        stored = self.entry.options.get(CONF_PANEL_SETTINGS) or {}
        settings: dict[str, int] = {}
        for key, (default, low, high) in PANEL_SETTINGS.items():
            try:
                value = int(stored.get(key, default))
            except (TypeError, ValueError):
                value = default
            settings[key] = max(low, min(high, value))
        return settings

    async def async_restart(self) -> None:
        """Reboot the panel with the firmware's own restart command.

        The panel drops off MQTT and comes back announcing itself online, and
        _on_lwt re-pushes the configuration when it does - so the tiles are
        rebuilt without anything else being done.
        """
        await mqtt.async_publish(
            self.hass, topic_cmnd(self.client_id), json.dumps({"restart": True})
        )

    @property
    def background_color(self) -> dict[str, int]:
        """Background colour to push, as the firmware's {"r", "g", "b"}.

        The firmware casts each channel to a byte, so an out-of-range number
        would wrap round to a different colour rather than being rejected.
        Clamping here is what stops a stored 300 becoming 44.
        """
        channels = normalize_rgb(self.entry.options.get(CONF_BACKGROUND_COLOR))
        return rgb_payload(channels or DEFAULT_BACKGROUND_COLOR)

    @property
    def icon_on_color(self) -> dict[str, int]:
        """Colour of an icon in its "on" state, as the firmware's {"r", "g", "b"}.

        The firmware reads pure black as "unset" and substitutes its default, so
        a stored black is sent as that default instead - the payload then says
        what the panel will actually show.
        """
        channels = normalize_rgb(self.entry.options.get(CONF_ICON_ON_COLOR))
        if channels is None or channels == BLACK:
            channels = DEFAULT_ICON_ON_COLOR
        return rgb_payload(channels)

    async def async_refresh_album_art(
        self, tile: dict[str, Any], *, force: bool = False
    ) -> bool:
        """Re-encode and push this tile's artwork if it changed.

        Returns True when the panel's idea of the art changed, so the caller
        knows whether the tile itself needs re-publishing. Uploading an image
        name the panel already holds updates every tile using it, so a plain
        track change needs no tile command at all - but the first upload does,
        because until then the tile has no background to reference.

        force=True re-uploads even when the revision matches, which is what a
        reconnect needs: the panel has forgotten every image, while this
        object still remembers sending them.
        """
        entity_id = tile.get(CONF_ENTITY_ID) or tile.get(CONF_ACTION_ENTITY)
        if not entity_id:
            return False

        name = art_image_name(entity_id)
        state = self.hass.states.get(entity_id)
        revision = art_revision(state)
        url = art_source_url(self.hass, state)

        if not url or revision is None:
            # Nothing playing, or a player with no artwork. Forget the name so
            # _augment_tile_state clears the background instead of leaving the
            # previous cover on screen.
            if self._album_art.pop(name, None) is not None:
                _LOGGER.debug(f"Album art for {entity_id} no longer available")
                return True
            return False

        if not force and self._album_art.get(name) == revision:
            return False

        payload = await async_build_art_payload(
            self.hass, url, name, self.album_art_budget
        )
        if payload is None:
            return False

        await mqtt.async_publish(
            self.hass, topic_cmnd(self.client_id), json.dumps(payload)
        )
        first_upload = name not in self._album_art
        self._album_art[name] = revision
        _LOGGER.debug(f"Pushed album art '{name}' for {entity_id}")
        return first_upload or force

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
        # async_push_config handles: conf/ → addImage → seed_state
        await self.async_push_config()

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

        # Common capability: subLabel source entity (any tile type)
        entity_ids.extend([
            t[CONF_SUBLABEL_ENTITY_ID]
            for t in self.tiles
            if t.get(CONF_SUBLABEL_ENTITY_ID)
        ])

        # indicator tile: optional secondary sensor
        entity_ids.extend([
            t[CONF_INDICATOR_SECONDARY_ENTITY_ID]
            for t in self.tiles
            if t.get(CONF_INDICATOR_SECONDARY_ENTITY_ID)
        ])

        entity_ids = list(dict.fromkeys(entity_ids))  # de-dupe, keep order

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

        # Display settings go first and always: the panel keeps whatever it was
        # last told, so sending defaults explicitly is what makes them defaults.
        conf: dict[str, Any] = {
            **self.panel_settings,
            "backgroundColorRgb": self.background_color,
            "iconOnColorRgb": self.icon_on_color,
            "screens": [],
        }
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
                
                # A tile's own colour; without one it inherits its screen's.
                tile_color = override_payload(t.get(CONF_BACKGROUND_COLOR))
                if tile_color is not None:
                    tile_conf["backgroundColorRgb"] = tile_color

                tiles_conf.append(tile_conf)
            
            screen_names = self.entry.options.get(CONF_SCREEN_NAMES, {})
            screen_conf: dict[str, Any] = {
                "screen": screen_idx,
                "label": screen_names.get(str(screen_idx), self.entry.title),
                "screenLayout": DEFAULT_LAYOUT,
                "tiles": tiles_conf,
            }
            # A screen's own colour; without one it inherits the panel's.
            screen_colors = self.entry.options.get(CONF_SCREEN_COLORS)
            screen_color = override_payload(
                screen_colors.get(str(screen_idx))
                if isinstance(screen_colors, dict)
                else None
            )
            if screen_color is not None:
                screen_conf["backgroundColorRgb"] = screen_color
            conf["screens"].append(screen_conf)

        await mqtt.async_publish(
            self.hass, topic_conf(self.client_id), json.dumps(conf)
        )
        # Let the panel apply the config before seeding tile states.
        await asyncio.sleep(1)
        # Step 1: register background images in panel memory before tiles reference them
        await self.async_push_images_to_panel()
        # Step 2: seed tile states (includes backgroundImage.name references)
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
                                _augment_tile_state(self.hass, state, tile, self.library, set(self._album_art))
                                payload_tiles.append(state)
                continue
            
            # Process hardcoded tiles with entity bindings
            tile_type = tile.get(CONF_TYPE)
            handler = TILE_TYPES.get(tile_type)
            if handler is None:
                continue
            state = handler["build_state"](self.hass, tile)
            if state is not None:
                _augment_tile_state(self.hass, state, tile, self.library, set(self._album_art))
                payload_tiles.append(state)
        
        if payload_tiles:
            await mqtt.async_publish(
                self.hass,
                topic_cmnd(self.client_id),
                json.dumps({"tiles": payload_tiles}),
            )

    async def async_push_images_to_panel(self) -> None:
        """Send background images AND custom icons actually used on THIS
        panel (Step 1 of the OXRS two-step process: addImage / addIcon).
        
        Images and icons live in the shared library (library.py), reusable
        across every configured panel - but each physical panel only needs
        the ones its OWN tiles actually reference, since RAM is limited and
        separate per device. This scans self.tiles for:
        - background_image_name (any tile type)
        - icon (any tile type) - but only those NOT starting with "_", i.e.
          not one of the firmware's own built-in icons, which need nothing
          sent for them at all
        - the other half of any bundled icon pair among those icons, since
          _augment_tile_state swaps to it when the tile's state flips
        and sends an addImage/addIcon command for each unique name found.
        
        This is called during setup after config push to ensure images/icons
        are available before tiles try to reference them by name. Nothing is
        persistent on the panel - it must all be resent every time the panel
        (re)connects. Per the OXRS docs, load order relative to conf/ doesn't
        matter ("shown after they are loaded, before or after configured"),
        so this can run either side of the conf/ push.
        """
        image_names = {
            t["background_image_name"]
            for t in self.tiles
            if t.get("background_image_name")
        }
        icon_names = {
            t[CONF_ICON]
            for t in self.tiles
            if t.get(CONF_ICON) and not t[CONF_ICON].startswith("_")
        }
        icon_names |= {
            partner
            for name in icon_names
            if (partner := self.library.paired_icon(name)) is not None
        }

        if not image_names and not icon_names:
            _LOGGER.debug("No shared images or icons referenced by this panel's tiles")
            await self._async_push_album_art()
            return

        _LOGGER.debug(
            f"Sending {len(image_names)} image(s) and {len(icon_names)} icon(s) "
            f"to panel (addImage / addIcon)..."
        )

        try:
            for name in image_names:
                image = self.library.get_image_by_name(name)
                if image is None:
                    _LOGGER.warning(f"Referenced image '{name}' not found in shared library")
                    continue
                payload = self.library.build_add_image_payload(image["id"])
                if not payload:
                    continue
                _LOGGER.debug(
                    f"Sending image '{name}' to panel "
                    f"(format: {image.get('format')}, size: {image.get('size')} bytes)"
                )
                await mqtt.async_publish(
                    self.hass, topic_cmnd(self.client_id), json.dumps(payload)
                )
                await asyncio.sleep(0.2)  # small delay to avoid overwhelming the panel

            for name in icon_names:
                icon = self.library.get_icon_by_name(name)
                if icon is None:
                    _LOGGER.warning(f"Referenced icon '{name}' not found in shared library")
                    continue
                payload = self.library.build_add_icon_payload(icon["id"])
                if not payload:
                    continue
                _LOGGER.debug(f"Sending icon '{name}' to panel (size: {icon.get('size')} bytes)")
                await mqtt.async_publish(
                    self.hass, topic_cmnd(self.client_id), json.dumps(payload)
                )
                await asyncio.sleep(0.2)

            _LOGGER.info(
                f"Sent {len(image_names)} image(s) and {len(icon_names)} icon(s) to panel"
            )

        except Exception as err:
            _LOGGER.error(
                f"Error sending images/icons to panel: {err}",
                exc_info=True
            )

        await self._async_push_album_art()

    async def _async_push_album_art(self) -> None:
        """(Re)upload artwork for every album-art tile on this panel.

        Always forces: this runs when the panel has just (re)connected, and a
        panel keeps no images across a restart, so a cached revision here says
        nothing about what the panel currently holds.
        """
        for tile in self.album_art_tiles:
            try:
                await self.async_refresh_album_art(tile, force=True)
            except Exception as err:
                _LOGGER.error(f"Error pushing album art: {err}", exc_info=True)

    async def _async_update_album_art_tile(
        self,
        handler: TileType,
        tile: dict[str, Any],
        build_tile: dict[str, Any],
    ) -> None:
        """Refresh artwork, then publish the tile that shows it.

        Ordered rather than fire-and-forget: on the first upload the tile can
        only reference the image once the panel has it.
        """
        try:
            await self.async_refresh_album_art(tile)
        except Exception as err:
            _LOGGER.error(f"Error refreshing album art: {err}", exc_info=True)

        state = handler["build_state"](self.hass, build_tile)
        if state is None:
            return
        _augment_tile_state(
            self.hass, state, tile, self.library, set(self._album_art)
        )
        await mqtt.async_publish(
            self.hass, topic_cmnd(self.client_id), json.dumps({"tiles": [state]})
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
        
        # OLD format: tiles with CONF_ENTITY_ID + CONF_TYPE — match on the
        # tile's primary entity OR either of its optional secondary entities
        # (subLabel source / indicator secondary sensor). Collect EVERY
        # matching tile, not just the first: subLabel sources in particular
        # are designed to be shared across many tiles (e.g. an "outdoor
        # temperature" sensor used as the subLabel on several screens), and
        # the same primary entity can also be bound to more than one tile.
        old_format_tiles = [
            t for t in self.tiles
            if CONF_TYPE in t
            and entity_id in (
                t.get(CONF_ENTITY_ID),
                t.get(CONF_SUBLABEL_ENTITY_ID),
                t.get(CONF_INDICATOR_SECONDARY_ENTITY_ID),
            )
        ]
        for old_format_tile in old_format_tiles:
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

            if tile.get(CONF_ALBUM_ART):
                # Artwork has to reach the panel before the tile can reference
                # it, so these go through one ordered task rather than the
                # fire-and-forget publish below. Cheap on a position update:
                # the refresh returns immediately when the art has not changed.
                self.hass.async_create_task(
                    self._async_update_album_art_tile(handler, tile, temp_tile)
                )
                continue

            state = handler["build_state"](self.hass, temp_tile)
            if state is not None:
                _augment_tile_state(self.hass, state, tile, self.library, set(self._album_art))
                self.hass.async_create_task(
                    mqtt.async_publish(
                        self.hass,
                        topic_cmnd(self.client_id),
                        json.dumps({"tiles": [state]}),
                    )
                )

    async def _handle_event_and_confirm(
        self,
        handler: TileType,
        handle_tile: dict[str, Any],
        build_tile: dict[str, Any],
        augment_tile: dict[str, Any],
        tile_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Apply an inbound touch event, then re-push the tile's true state.

        Bug this fixes: turning a light on via a tap (e.g. a CCT or RGBW
        tile's toggle) calls light.toggle/turn_on with no explicit
        brightness, so the light comes on at whatever HA/the device decides.
        The panel has no way to know that value ahead of time, and if the
        entity's brightness attribute isn't populated yet on the very first
        state-changed event (common with some integrations - the on/off
        flag lands before the attributes), the panel can end up stuck
        showing 0% brightness even though the light is genuinely on.

        Relying solely on the live entity-change listener (_on_entity_change)
        assumes a follow-up state-changed event will arrive with the correct
        attributes - which isn't guaranteed to happen promptly, or at all,
        depending on the integration. This adds a guaranteed correction
        shortly after every touch event, on top of that listener: wait for
        things to settle, then rebuild the tile's state from whatever HA
        reports at that point and push it, regardless of whether another
        update already arrived in the meantime (harmless if so - it's an
        idempotent re-send of the current truth).
        """
        await handler["handle_event"](self.hass, handle_tile, payload)
        await asyncio.sleep(0.6)
        state = handler["build_state"](self.hass, build_tile)
        if state is not None:
            _augment_tile_state(self.hass, state, augment_tile, self.library, set(self._album_art))
            await mqtt.async_publish(
                self.hass,
                topic_cmnd(self.client_id),
                json.dumps({"tiles": [state]}),
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
        
        # Only tile events name both a screen and a tile. The panel also
        # publishes backlight, lock-state and message-box events (neither
        # field) and screen-change events (screen only), and defaulting the
        # missing ones to 1 handled each of those as a touch on tile 1 - which
        # re-pushed that tile's state 0.6 s after every sleep, wake and screen
        # change.
        if "screen" not in payload or "tile" not in payload:
            _LOGGER.debug(f"Ignoring non-tile panel event: {payload.get('type')}")
            return
        screen = payload["screen"]
        tile_idx = payload["tile"]
        
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
                    self._handle_event_and_confirm(
                        handler, temp_tile, temp_tile, tile, matching_type, payload
                    )
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
                self._handle_event_and_confirm(
                    handler, tile, tile, tile, tile_type, payload
                )
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
