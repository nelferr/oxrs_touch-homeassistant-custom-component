# Tile Catalog Mapping — ESPControl card types → OXRS tiles

Build reference for expanding `TILE_TYPES`. Each entry below says which OXRS
firmware style a card rides on, which HA entities the picker should offer, and
what `build_state` / `handle_event` have to do. Work through it card by card;
nothing here needs firmware changes unless explicitly marked.

**Sources**
- Card catalog: <https://jtenniswood.github.io/espcontrol/card-types/> (ESPHome project — used only as a catalog of *what a panel tile can usefully do*, not as a spec)
- OXRS protocol: <https://github.com/OXRS-IO/OXRS-IO-WEBSITE> → `docs/docs/firmware/touch-panel-esp32.md` (authoritative for everything in Part A)
- This integration: `oxrs_touchpanel/tiles.py`, `config_flow.py`, `hub.py`, `library.py`

---

## Part A — What OXRS actually gives us

### A.1 Firmware tile styles

All 15 styles the TP32 firmware implements. Seven are unused by this
integration today, and several of them are exact fits for catalog items I
previously assumed were impossible.

| Style | Used today | Interaction model |
| :--- | :--- | :--- |
| `button` | ✅ `button` | tap / hold / release |
| `buttonUpDownLevel` | ✅ `updownlevel` | up/down + firmware-tracked level |
| `buttonSlider` | ✅ `slider` | drag to set level |
| `buttonUpDown` | ✅ `updown`, `volume` | up/down, direction only |
| `buttonLeftRight` | ❌ **unused** | left/right, direction only |
| `buttonPrevNext` | ❌ **unused** | prev/next, styled for playlists |
| `indicator` | ✅ `indicator` | display-only, no touch events |
| `feed` | ❌ **unused** | opens a 5-message scrollable feed screen |
| `colorPickerRgbCct` | ✅ `rgbw` | colour wheel + CCT slider |
| `colorPickerRgb` | ❌ **unused** | colour wheel only |
| `colorPickerCct` | ✅ `cct` | CCT slider only |
| `dropDown` | ✅ `select` | opens a full-screen option list |
| `buttonSelector` | ❌ **unused** | options cycle *inside* the tile |
| `remote` | ❌ **unused** | opens a d-pad remote (home/info/back/list/ok/up/down/left/right) |
| `link` | ❌ **unused** | jumps to another screen on tap |
| `keyPad` | ❌ **unused** | opens a PIN keypad, returns `keyCode` |
| `thermostat` | ✅ `thermostat` | arc + mode list |

### A.2 Capabilities available on *any* tile

These are style-independent and are the main reason most catalog items need no
new firmware support. Sent on `cmnd/` unless noted.

| Field | Effect |
| :--- | :--- |
| `state` | `"on"` / `"off"` — drives the tile's lit/unlit appearance |
| `level` | `0-100` fill from the bottom. Needs `levelBottom` / `levelTop` in `conf/` |
| `text` | Replaces the icon with text. Supports colour: `"#RRGGBB Open#"` |
| `subLabel` | Free text under the label — no charset restriction |
| `icon` | Swappable at runtime, not just in `conf/` |
| `iconColorRgb` | `{r,g,b}` per-tile icon colour |
| `backgroundColorRgb` | `{r,g,b}` per-tile background |
| `backgroundImage` | `{"name": …}`, after an `addImage` — see `library.py` |
| `tag` | Arbitrary string echoed back in the tile's `stat/` event |
| `right` / `left` (conf) | Grid-cell spanning for wider tiles |

Panel-level, not per-tile:
- `{"screen": {"load": N}}` on `cmnd/` — jump to a screen from HA
- `{"screens": [{"screen": N, "footer": {"left": …, "center": …, "right": …}}]}` — the footer bar
- `{"noActivitySecondsToLock": N}` on `conf/` — keypad-blocked screen lock

**Caveat on `text`:** the OXRS docs contradict themselves (line 161 says empty
string restores the icon, line 177 says it clears it). `hub.py` already
resolves this against firmware source in `_augment_tile_state` — **non-empty
hides the icon, empty restores it**. Follow the code, not the docs.

### A.3 The recipe for adding a tile type

1. `tiles.py` — add a `TILE_TYPES` entry: `style`, `domain`, `icon`, `label`,
   `config_extra`, `build_state`, `handle_event`.
2. `config_flow.py` — add a branch to `_get_entity_filter_for_tile_type` when
   the filter needs to be narrower than a bare domain match (device_class,
   supported_features, attribute presence).
3. Nothing else. `hub.py` drives everything generically off the registry.

Two things the registry does **not** support yet, needed by a few cards below:
- **Periodic re-push** (countdown timers, media track position). Needs an
  `async_track_time_interval` loop in `hub.py`, opt-in per tile type.
- **Service call responses** (weather forecasts). Needs
  `hass.services.async_call(..., return_response=True)`, which the current
  `blocking=False` helper calls don't allow.

---

## Part B — Catalog at a glance

| ESPControl card | OXRS style | Domain | Status |
| :--- | :--- | :--- | :--- |
| Switch | `button` | switch, input_boolean, light, fan | extend `button` |
| Lights | colour pickers / `buttonSlider` | light | mostly done, add `colorPickerRgb` |
| Action | `button` | scene, script, automation, button, input_button | extend `button` |
| Vacuum | `buttonSelector` | vacuum | new |
| Lawn Mower | `buttonSelector` | lawn_mower | new |
| Option Select | `dropDown` / `buttonSelector` | select, input_select | done, add in-tile variant |
| Trigger | `button` | none | use flexible actions instead |
| Webhook | — | — | out of scope, use HA `rest_command` |
| Sensor | `indicator` / `button`+`text` | sensor | done for numeric, new for text |
| Doors & Windows | `button` (read-only) | binary_sensor | new |
| Presence | `button` (read-only) | binary_sensor | new |
| Slider | `buttonSlider` | light, fan, number, input_number | extend `slider` |
| Fans | `buttonSelector` + `buttonLeftRight` | fan | new |
| Cover | `buttonSlider`, `buttonLeftRight` | cover | extend `updown*` |
| Garage Door | `button` | cover (device_class garage) | new |
| Gate | `button` | cover (device_class gate) | new |
| Lock | `button` or `keyPad` | lock | new |
| Alarm | `keyPad` + `buttonSelector` | alarm_control_panel | new |
| Timer | `button` + `level` | timer | new, needs ticking |
| Date & Time | footer / `indicator` | none | new, panel-level |
| World Clock | `indicator` | none | new |
| Weather | `indicator` | weather | new, forecast needs response API |
| Camera | `backgroundImage` | camera, image | experimental, 4 KB limit |
| Wifi Sharing | `backgroundImage` (QR) | none | new, feasible |
| Media | `buttonPrevNext`, `remote`, … | media_player | extend `volume` heavily |
| Climate | `thermostat` + `dropDown` | climate | done, add mode tiles |
| Internal Switches | — | — | **N/A** — TP32 has no relays |
| Screen Lock | `keyPad` + device conf | none | new, panel-level |
| Subpage | `link` | optional state entity | new |

---

## Part C — Per-card build specs

### 1. Switch
**Extends** `button` · **Style** `button` · **Domains** `switch`, `input_boolean`, `light`, `fan`

- **Filter:** `entity.domain in ("switch", "input_boolean", "light", "fan")`.
  For `light`, only offer here when the light is *not* dimmable, otherwise the
  Lights cards are a better fit.
- **build_state:** `state` = `"on"` if `state.state == "on"`.
- **handle_event:** `type == "button"`, `event == "single"` → `homeassistant.toggle`.
  Using the generic `homeassistant` domain avoids growing `_BUTTON_SERVICES`
  per domain.
- **Icon:** `_onoff`.

### 2. Lights
**Mostly done** · **Styles** `colorPickerRgbCct` (`rgbw`), `colorPickerCct` (`cct`), `buttonSlider` (`slider`), `buttonUpDownLevel` (`updownlevel`)

- **Missing:** `colorPickerRgb` for lights that support `hs`/`rgb` but no colour
  temperature. Filter: `"hs" in color_modes and "color_temp" not in color_modes`.
  Copy `_rgbw_build_state`, drop `colorKelvin`, send `colorRgb` only.
- **Colour presets:** `buttonSelector` over the light's `effect_list`, or over a
  user-chosen list of scenes. `handle_event` → `light.turn_on` with `effect`.

### 3. Action
**Extends** `button` · **Style** `button`

- **Filter:** add `automation` to the existing tuple.
- **handle_event:** `automation` → `automation.trigger`.
- **Set Number Helper:** separate type — style `buttonUpDownLevel`, domains
  `number` / `input_number`. `config_extra` reads `min`/`max`/`step` attributes
  into `levelBottom` / `levelTop` (this is exactly what `config_extra` exists
  for). `handle_event` on `type == "level"` → `number.set_value`.
- **Trigger / Local Action:** don't build these as tile types. The repo's
  flexible-action format (`CONF_ACTIONS`, HA `Script` sequences in `models.py`)
  already does arbitrary event firing and service sequencing, more powerfully
  than a fixed card would. Document it instead.

### 4. Vacuum
**New** · **Style** `buttonSelector` · **Domain** `vacuum`

- **conf:** style `buttonSelector`, icon from the shared library.
- **build_state:** `selectorList` = the commands supported per
  `supported_features` (`["Start", "Pause", "Dock", "Locate"]`),
  `selectorSelect` = index reflecting current activity (`cleaning` → Start,
  `docked` → Dock, …), `subLabel` = `f"{state.state} · {battery_level}%"`.
- **handle_event:** `type == "selector"`, `event == "selection"` → map the
  1-based index back to `vacuum.start` / `pause` / `return_to_base` / `locate`.
- **Note:** `buttonSelector` shows options in-tile, so keep labels ≤ 6 chars.

### 5. Lawn Mower
**New** · **Style** `buttonSelector` · **Domain** `lawn_mower`

Identical to Vacuum with `["Mow", "Pause", "Dock"]` →
`lawn_mower.start_mowing` / `pause` / `dock`. `subLabel` = activity.

### 6. Option Select
**Done as** `select` (`dropDown`)

- **Add:** a `buttonSelector` variant for short option lists (≤ 4 options,
  ≤ 6 chars each) so the user doesn't leave the screen. Same `build_state`
  logic, but `selectorList` / `selectorSelect` instead of
  `dropDownList` / `dropDownSelect`, and events arrive as
  `type == "selector"` rather than `type == "dropDown"`.

### 7. Trigger / 8. Webhook
**Out of scope.** Covered better by flexible actions (`CONF_ACTIONS`) plus HA's
own `rest_command` / `webhook` integrations. Adding panel-side HTTP would put
network config on the panel that HA already owns.

### 9. Sensor
**Done for numeric** (`indicator`) · **New for text**

- The `indicator` style restricts `number.value` to `0-9 + - . :`, which is why
  `_format_indicator_value` strips everything else. For non-numeric sensors use
  a **different style**: `button` with `text` set to the state, coloured via
  `"#RRGGBB {state}#"`, `handle_event` a no-op.
- **Duration formatting:** for `device_class: duration` or timestamp sensors,
  format as `"H:MM"` — the `indicator` charset allows `:`.

### 10. Doors & Windows
**New** · **Style** `button`, read-only · **Domain** `binary_sensor`

- **Filter:** `device_class in ("door", "window", "garage_door", "opening")`.
- **build_state:** `state` = `"on"` when open; `icon` swapped `_door` / `_window`;
  `iconColorRgb` red `{255,60,60}` when open, neutral when closed;
  `subLabel` = `"Open"` / `"Closed"` (or last-changed, relative).
- **handle_event:** no-op — display only, like `_indicator_handle_event`.
- **Why `button` and not `indicator`:** `indicator` can't render words, and a
  lit/unlit coloured icon reads better across a room than text.

### 11. Presence
**New** · Same as Doors & Windows with
`device_class in ("motion", "occupancy", "presence")`, icons from the shared
library, `subLabel` = `"Detected"` / `"Clear"`.

### 12. Slider
**Extends** `slider` · **Style** `buttonSlider`

- **Add domains:** `fan` (`fan.set_percentage`, level = `percentage`),
  `number` / `input_number` (`config_extra` from `min`/`max` as in card 3).
- Keep the existing light behaviour unchanged.

### 13. Fans
**New** · **Styles** `buttonSelector`, `buttonLeftRight`, `button`

- **Speed/preset:** `buttonSelector` over `preset_modes`, or over
  `["Off","Low","Med","High"]` mapped to percentage steps.
  → `fan.set_preset_mode` / `fan.set_percentage`.
- **Direction:** `buttonLeftRight` — the unused style maps cleanly.
  `type == "left"` → `fan.set_direction` `reverse`, `"right"` → `forward`.
- **Oscillation:** plain `button` → `fan.oscillate`.
- **Gate each on `supported_features`** in the picker filter.

### 14. Cover
**Extends** `updown` / `updownlevel`

- **Position:** `buttonSlider` → `cover.set_cover_position`. Gate on
  `CoverEntityFeature.SET_POSITION`.
- **Tilt:** `buttonLeftRight` → `cover.open_cover_tilt` / `close_cover_tilt`,
  gated on the tilt features. Another natural use of the unused style.
- **Single-action Open / Close / Stop:** `button` type with the action chosen at
  config time, for users who want three discrete tiles.

### 15. Garage Door
**New** · **Style** `button` · **Domain** `cover`, `device_class == "garage"`

- **build_state:** `state` = `"on"` when not closed; `subLabel` = `state.state`
  (covers `opening` / `closing`); `iconColorRgb` amber while moving.
- **handle_event:** tap toggles `cover.open_cover` / `close_cover`; if currently
  `opening`/`closing`, `cover.stop_cover` — reuse the logic already in
  `_updown_handle_event`.

### 16. Gate
**New** · Same as Garage Door with `device_class == "gate"`.

### 17. Lock
**New** · **Style** `button` (simple) or `keyPad` (code-protected) · **Domain** `lock`

Simple variant:
- **build_state:** `state` = `"on"` when `state.state == "locked"`;
  `icon` swapped `_locked` / `_unlocked`; `subLabel` = `"Locked"` /
  `"Unlocked"` / `"Jammed"`; `iconColorRgb` red when jammed.
- **handle_event:** tap → `lock.lock` / `lock.unlock` based on current state.
  Don't assume `lock.toggle` exists on the user's HA version; branch explicitly.

Keypad variant (uses the unused `keyPad` style):
- Panel opens a PIN screen and returns `{"type": "button", "event": "key",
  "keyCode": "1234"}` on `stat/`.
- HA verifies the code, then replies on `cmnd/` with
  `{"keyPad": {"state": "unlocked"|"failed", "text": …, "iconColorRgb": …}}`
  and calls `lock.unlock`.
- **Security caveat, document it loudly:** `keyCode` crosses MQTT in **plain
  text**. Store the expected code in the config entry, never log it, and treat
  this as convenience-grade, not security-grade.

### 18. Alarm
**New** · **Styles** `keyPad` + `buttonSelector` · **Domain** `alarm_control_panel`

- **Arm mode selection:** `buttonSelector` with `selectorList` built from
  `supported_features` (`["Disarm","Home","Away","Night"]`), `selectorSelect`
  from current state.
- **Disarm with code:** `keyPad`, passing the entered `keyCode` straight through
  as the `code` parameter to `alarm_control_panel.alarm_disarm` — HA does the
  verification, which is better than verifying panel-side.
- **build_state:** `subLabel` = state (`armed_away`, `pending`, `triggered`),
  `iconColorRgb` green/amber/red by state.
- Same plaintext caveat as Lock.

### 19. Timer
**New** · **Style** `button` + `level` · **Domain** `timer`

- **config_extra:** `levelBottom: 0`, `levelTop: 100`.
- **build_state:** `level` = percentage remaining, `subLabel` = `"4:32"`,
  `state` = `"on"` while active.
- **handle_event:** tap → `timer.start` when idle, `timer.cancel` when active;
  hold → `timer.pause`.
- **Blocker:** the countdown only advances if something re-pushes it. Needs the
  periodic re-push hook from A.3 — do this card *after* that hook exists.

### 20. Date & Time
**New** · **Panel-level, not a tile type**

- Best fit is the per-screen **footer**: push
  `{"screens":[{"screen":N,"footer":{"left":"Wed 07 Sep","right":"20:49"}}]}`
  on a one-minute interval.
- A clock **tile** also works via `indicator` — the charset allows `:`, so
  `{"number": {"value": "20:49"}}` renders fine.
- Needs the periodic re-push hook.

### 21. World Clock
**New** · **Style** `indicator` · no entity

Same as above with a user-chosen tz (`zoneinfo`), `label` = city name.

### 22. Weather
**New** · **Style** `indicator` · **Domain** `weather`

- **build_state:** `number.value` = current temp, `subValue` = humidity or
  tomorrow's high; `subLabel` = condition text; `icon` swapped per condition
  from the shared icon library (this is a good consumer of `library.py`).
- **Forecast modes blocker:** `weather.get_forecasts` is a response-returning
  service. Needs `return_response=True` and an `await`ed, blocking call — the
  current fire-and-forget pattern in `tiles.py` can't express it.

### 23. Camera
**New, experimental** · **Style** any + `backgroundImage` · **Domains** `camera`, `image`

- Pipeline: `camera.async_get_image` → downscale → PNG → base64 → `addImage`
  → tile references it by name. `library.py` already has the second half.
- **Hard limit:** base64 payload must stay under **4096 bytes** or the panel
  crashes (`MAX_ENCODED_SIZE` in `library.py`). That's ~3 KB of PNG — a heavily
  downscaled, low-colour thumbnail. Needs a Pillow dependency for the resize.
- Treat as a "latest snapshot" tile refreshed on a slow interval, not a stream.

### 24. Wifi Sharing
**New** · **Style** any + `backgroundImage` · no entity

- Generate a QR of `WIFI:S:<ssid>;T:WPA;P:<pass>;;`, render as 1-bit PNG, push
  through the same `addImage` path.
- Unlike Camera, this genuinely fits the 4 KB budget — QR codes compress well.
- Store the guest credentials in the config entry; never log them.

### 25. Media
**Extends** `volume` heavily · **Domain** `media_player`

The largest card. Build as separate tile types sharing one entity filter:

| Mode | Style | Logic |
| :--- | :--- | :--- |
| Play / Pause | `button` | `state` from `playing`; tap → `media_play_pause` |
| Prev / Next | `buttonPrevNext` | `type == "prev"` / `"next"` → `media_previous_track` / `media_next_track` |
| Volume (step) | `buttonUpDown` | existing `volume` type, unchanged |
| Volume (slider) | `buttonSlider` | `level` = `volume_level * 100` → `volume_set` |
| Track position | any + `level` | `level` = `media_position / media_duration`; `subLabel` = `"1:23 / 3:45"` |
| Now playing | `button` + `text` | `text` = title, `subLabel` = artist |
| Cover art | any + `backgroundImage` | `entity_picture` → same 4 KB pipeline as Camera |
| Source / content | `dropDown` | existing `select` type already covers `source_list` |
| Remote (d-pad) | `remote` | opens the firmware remote screen |

- **`buttonPrevNext`** is purpose-built for this — the firmware even documents it
  with a `_music` icon and "Skip track" label.
- **`remote`** returns `type` ∈ `home/info/back/list/ok/up/down/left/right`. Map
  to HA's `remote.send_command` for a bound `remote` entity, or to
  `media_player` equivalents for players that expose them (Kodi, Android TV).
  Worth its own tile type with domain `remote`.
- **Track position** needs the periodic re-push hook.
- **Speaker group** (join/unjoin): low value on a 3×3 grid, skip.

### 26. Climate
**Done as** `thermostat` · **Add mode tiles**

- **HVAC mode:** `buttonSelector` or `dropDown` over `hvac_modes`
  → `climate.set_hvac_mode`. The `thermostat` style already carries `modeList`,
  so this is only worth it as a standalone tile.
- **Preset / fan mode:** `dropDown` over `preset_modes` / `fan_modes`.
- **Target temp only:** `buttonUpDownLevel` with `config_extra` from
  `min_temp` / `max_temp` (note the tenths-of-a-degree convention already used
  in `_thermostat_build_state`).

### 27. Internal Switches
**N/A.** The TP32 firmware documents no relay hardware or relay payloads. Drop
this card unless a future panel revision adds them.

### 28. Screen Lock
**New** · **Panel-level**

- `{"noActivitySecondsToLock": N}` on `conf/` makes the panel demand a PIN after
  inactivity, with the `keyPad` style as the unlock UI.
- Expose as a config-entry option plus, optionally, an HA `switch` entity on the
  panel device — this integration already creates panel-level entities
  (`binary_sensor.py`, `button.py`), so it fits the existing pattern.

### 29. Subpage
**New** · **Style** `link` · optional state entity

- **conf:** `{"style": "link", "link": <target screen number>}` — `link` is
  required and is the screen the tap loads. The firmware handles navigation
  itself; no `stat/` round-trip needed.
- **Typed subpages** (ESPControl's Switch/Lights/Climate/… variants) are just a
  `link` tile with an optional bound entity driving `state` and `subLabel`
  ("3 lights on", "21.4 °C"). One tile type with an optional entity covers all
  16 of their variants.
- **HA-driven navigation** is the mirror image: `{"screen": {"load": N}}` on
  `cmnd/` lets an automation push the panel to a screen. Worth exposing as a
  service or a `button` entity per screen.
- Screen numbers already exist in the config flow (`CONF_SCREEN`,
  `CONF_SCREEN_NAMES`), so the picker can offer real screen names.

---

## Part D — Beyond the catalog

Firmware features with no ESPControl equivalent, worth considering anyway:

- **`feed`** — a 5-message scrollable feed screen, with coloured headings
  (`"#ff0000 Heading#"`). Natural home for HA `persistent_notification`s, or a
  rolling log of a text sensor. Push with
  `{"messageFeed": {"addPost": {"id": …, "head": …, "body": …}}}`.
- **`tag`** — arbitrary string echoed back in every `stat/` event for that tile.
  Could carry the tile's HA config id, removing the screen/tile lookup in
  `_on_stat`.
- **Tile spanning** (`left` / `right` in `conf/`) — wider tiles for media or
  weather, currently unused by the config flow's fixed 3×3 grid.

## Part E — Suggested build order

1. **Read-only cards first** — Doors & Windows, Presence, text Sensor. No new
   firmware styles, no new infrastructure, immediate UX win from the filtered
   picker.
2. **Simple control cards** — Lock (button variant), Garage Door, Gate, Switch
   and Action domain extensions.
3. **Unused-style cards** — Subpage (`link`), Fans direction (`buttonLeftRight`),
   Media prev/next (`buttonPrevNext`), Vacuum / Lawn Mower / Fans speed
   (`buttonSelector`). Each proves out one new style.
4. **Infrastructure, then the cards that need it** — add the periodic re-push
   hook, then Timer, Date & Time, Media track position.
5. **Image pipeline** — Wifi Sharing first (fits the 4 KB budget), then Cover
   Art and Camera as experiments.
6. **Code-entry cards** — Alarm and Lock keypad variants, once the plaintext
   `keyCode` handling has been reviewed.
