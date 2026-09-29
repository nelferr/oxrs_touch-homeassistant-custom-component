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
| `span` `{right, down}` (conf) | Cells a tile covers, anchored at its top-left cell. The firmware clips an overflow but does NOT reject overlaps (tiles just stack) |

Panel-level, not per-tile:
- `{"screen": {"load": N}}` on `cmnd/` — jump to a screen from HA
- `{"screens": [{"screen": N, "footer": {"left": …, "center": …, "right": …}}]}` — the footer bar
- `{"noActivitySecondsToLock": N}` on `conf/` — keypad-blocked screen lock
- The rest of the panel's display settings also arrive on `conf/`; the integration now
  sends all of them — see A.5

**Caveat on `text`:** the OXRS docs contradict themselves (line 161 says empty
string restores the icon, line 177 says it clears it). `hub.py` already
resolves this against firmware source in `_augment_tile_state` — **non-empty
hides the icon, empty restores it**. Follow the code, not the docs.

### A.3 The recipe for adding a tile type

1. `tiles.py` — add a `TILE_TYPES` entry: `style`, `domain`, `icon`, `label`,
   `config_extra`, `build_state`, `handle_event`, and optionally
   `device_class` to narrow the picker past the domain, `integration` to tie
   the type to one integration (offered only while it's loaded), and
   `suggested_icons` to lead the icon picker.
2. If the card needs new artwork, add it to `oxrs_touchpanel/bundled_icons.json`
   (and to `ICON_STATE_PAIRS` in `library.py` if it pictures an on/off pair).
3. Nothing else. `hub.py` drives everything generically off the registry, and
   the config flow builds the type dropdown, the entity picker and the icon
   picker from it.

### A.5 Panel display settings

The firmware has eight panel-level settings: five that keep a screen from being left lit
indefinitely, how often the panel reports its sensors, and two colours. They are the same ones its admin page shows (the page renders the
`configSchema` the device announces; none of them are in the page's own HTML),
and they arrive on `conf/`. (`climateUpdateSeconds` is defined in the WT32 library, not
`main.cpp`, and the device only lists it when it has an SHT20 or an S3 chip.) The options menu's **Panel display settings** step
sets them; `PANEL_SETTINGS` in `const.py` is the one table holding the firmware
key, default and limits, and both the form and the hub read from it.

| Key | Default | Range | Effect |
| :--- | ---: | :--- | :--- |
| `noActivitySecondsToSleep` | 0 | 0–3600 | backlight off; a touch wakes it |
| `noActivitySecondsToHome` | 0 | 0–600 | close pop-ups, return to the home screen |
| `noActivitySecondsToLock` | 0 | 0–3600 | show the PIN keypad |
| `tileBrightnessOn` | 100 | 75–100 % | tiles in their on state |
| `tileBrightnessOff` | 10 | 0–25 % | tiles in their off state |
| `climateUpdateSeconds` | 60 | 0–86400 | how often temperature/humidity are reported; 0 stops them |

- **Background colour** (`backgroundColorRgb`, `{"r","g","b"}` 0-255) is the seventh
  setting, chosen with HA's colour picker rather than three number fields and stored
  as `[r, g, b]`. It applies to every screen, and to every tile, that has no colour of
  its own - see the cascade below for setting those. Default black. The firmware treats pure black as "unset" and
  resolves it to its default, also black, so choosing black and choosing nothing are
  the same. The firmware casts each channel to a byte, so an out-of-range number
  would wrap round to a different colour; the hub clamps to 0-255 first.
- **Temperature correction (v1.14.3)** is not a firmware setting - the panel is never told
  about it. `CONF_TEMPERATURE_OFFSET`, degrees Celsius, default 0, range -20 to +20, step
  0.5. Subtracted from `tele.temperature` before it reaches the Temperature sensor
  (`hub.py`, `_on_tele`), because the panel's sensor sits next to its own screen and
  electronics and commonly reads a few degrees above the room. Humidity and the CPU
  temperature sensor are not touched. A non-numeric reading is passed through unchanged
  rather than raising.
- **Copy settings from another panel (v1.14.4)**, a menu option (`config_flow.py`,
  `async_step_copy_from_panel`) offered only when more than one panel is configured.
  Always copies the display preferences (`panel_settings`, background colour, icon-on
  colour) from the chosen panel. Copies screens, tiles and album art sizing too, but
  ONLY when the source panel has the same grid (`layout_from_data`) as this one - a
  tile's position number is row-major and means a different cell on a grid with a
  different column count (see the grid-numbering note above), and a position beyond
  the target's cell count has no cell at all, which the firmware does not define
  behaviour for; album art sizing rides with tiles since a size safe on one board's
  memory may not be on another's. A key the source never set clears the target's own
  value, so the copy is complete, not a merge. Temperature correction is never copied -
  it corrects for one sensor's own bias, not a shared preference. This replaces
  settings on the panel being edited; it never changes the source panel.
- **Colours cascade tile -> screen -> panel.** Three levels, one firmware key
  (`backgroundColorRgb`) at each. At the screen and tile levels the firmware reads
  pure black as "unset" and inherits from the level above, so black is how a screen
  or tile says "no colour of its own" and this integration never stores or sends
  it. The consequence is that a screen cannot be painted black over a non-black
  panel colour, nor a tile black on a coloured screen.
  - *Panel* colour: **Panel display settings**.
  - *Screen* colour: **Screen name and colour**, a two-step flow (pick the screen,
    then name and colour) so the picker shows that screen's current colour.
  - *Tile* colour: the picker on the add-tile form.
- **Icon "on" colour** (`iconOnColorRgb`, default R91 G190 B91, light green) is
  panel-wide and chosen with the same picker. The firmware also reads black as
  unset here and substitutes that default, so the hub sends the default rather
  than a black it would ignore.
- **0 disables a timeout.** On the defaults the panel never sleeps, which is what
  leaves a static screen lit; the integration defaults to the firmware's values
  rather than choosing a sleep time for the user.
- **There is no timed dim.** Sleep cuts the backlight from its current level
  straight to 0. The `backlight` command on `cmnd/` (`brightness` 1–100, or
  `state` `sleep`/`awake`) and the tile brightness settings are the nearest
  things to dimming.
- **All eight are sent on every `conf/` push, defaults included,** because the
  panel keeps whatever it was last told. That also means a value set on the
  admin page is overwritten by this integration's value the next time the panel
  connects.
- Stored values are clamped to the firmware's limits before sending, so an old
  or hand-edited option can never push something the panel would reject.

### A.6 Editing a tile

The options menu's **Edit a tile** changes an existing tile in place, in four steps:
choose the tile, its details, (playlists tiles only) its playlists, then its
background image. Nothing is saved until the last step, so closing the dialog
part-way changes nothing.

| Editable | Not editable |
| :--- | :--- |
| position (within the screen), entity, label, icon, sub-label source, background colour, background image; album art (transport); secondary sensor (indicator); playlists (playlists) | the screen and the tile type |

The screen and type are what the tile *is* - the form, the entity filter and the
icon suggestions all hang off the type - so changing either means removing the
tile and adding a new one. Tiles without a known type (the unused
flexible-actions format) are not offered.

- **One form builder.** Adding and editing share `_tile_details_schema`, so the two
  forms cannot drift. The add form was checked field-for-field against its
  pre-refactor output for all 15 tile types and is identical.
- **Optional fields can be cleared.** A sub-label source or secondary sensor is
  pre-filled with `suggested_value`, not a default, so emptying the field removes
  it; album art is a boolean, and a black colour removes the tile's own colour.
- **The current entity and icon always stay selectable.** HA validates a submitted
  entity against `include_entities`, which drops anything unavailable, and a
  `SelectSelector` rejects a value outside its options. A tile whose entity is
  momentarily unavailable, or whose icon was deleted from the library, would
  otherwise give a form that cannot be submitted.
- **Playlists.** Pre-ticked with the tile's current playlists (those still in
  Music Assistant). If Music Assistant can't be reached the step is skipped and
  the tile keeps its playlists, so a label can still be changed while it is down.
  Adding a tile aborts in that case, having nothing to fall back on.
- **A background image deleted from the library** shows as "none" when editing, and
  saving drops the dead reference.

### A.7 Rebooting a panel, and cleaning up when one is removed

**Reboot button.** A **Reboot** button sits in the device's Configuration group, next to
Push configuration. It publishes `{"restart": true}` to `cmnd/<id>`, which the firmware
handles by calling `ESP.restart()` (`OXRS-IO-WT32-ESP32-LIB`, `OXRS_WT32.cpp`). It is not
retained - a retained restart would reboot the panel on every reconnect. The panel drops
off MQTT and comes back announcing itself online, and `_on_lwt` re-pushes the configuration
when it does; if the tiles do not return, **Push configuration** rebuilds them.

**Removal cleanup.** A panel publishes two RETAINED messages
(`OXRS-IO-MQTT-ESP32-LIB`): `stat/<id>/adopt` and `stat/<id>/lwt`. The manifest's
`"mqtt": ["stat/+/adopt"]` is what makes HA offer a panel for setup, and a retained message
is redelivered on every HA start and MQTT reconnect - so a panel that has been removed, or
was never really there, is "discovered" again indefinitely even with nothing on the network
publishing it. `async_remove_entry` (which HA calls after unloading a removed entry) now
deletes both, by publishing an empty retained payload to each.

- **A panel that is still online is left alone.** `retained.py` reads the retained LWT first;
  `{"online": true}` means the messages are not stale, and they are how a panel is found
  again if it is deleted and re-added. Anything else - offline, malformed, or no LWT at all -
  is treated as gone.
- **It can never block a removal.** MQTT being down, or the broker refusing, is logged with
  the manual fix (publish an empty retained message to `stat/<id>/adopt`) and swallowed.
- **Only removal triggers it.** Ghosts that were *discovered but never added* have no entry to
  remove, so this cannot reach them: use Ignore, or publish an empty retained message to
  their `stat/<id>/adopt` (HA's MQTT "Publish a packet" has a Retain switch).

### A.8 Board detection and the tile grid

A panel's adopt message (`stat/<id>/adopt`, retained) carries `firmware.hardware`, set from the
firmware's `FW_HARDWARE` build flag. Every build environment defines it, but the firmware emits it
only `#if defined(FW_HARDWARE)`, so a custom build may omit it.

| `hardware` | Screen | Native grid |
| :--- | :--- | :--- |
| `WT32S3-86S`, `WT32S3-86V` | 480 x 480 | 3 x 3 |
| `WT32-SC01`, `WT32-SC01-PLUS` | 320 x 480 | 2 x 3 (2 columns, 3 rows) |

(From the firmware's own defaults in `globalDefines.h` and `platformio.ini`; the SC01 family sets no
`SCREEN_*` flags and so takes the defaults.) `boards.py` holds the table.

- **New panels only.** The grid is decided once, when a panel is added, and stored in its config
  entry (`layout`, plus `hardware`). Discovery reads the board from the adopt message; adding by
  hand asks for it from a short list, defaulting to "other" (3 x 3). An unknown or missing board is 3 x 3.
- **Existing panels are never offered it.** An entry with no stored `layout` keeps the 3 x 3 the
  integration always sent. The reason is that **tile numbers are row-major, so they depend on the
  column count**: position 4 is row 2 column 1 on three columns but row 2 column 2 on two. Changing a
  grid under existing tiles would move every one of them and push some off the panel, so there is no
  conversion step and none is needed. The way onto the native grid is to remove and re-add the panel,
  which loses its tiles.
- **A warning, not a change.** If a panel later reports a different board than it was added as, the
  hub logs a warning once and changes nothing.
- The board is shown as the device's hardware version.

### A.9 Tile sizes

A tile can cover more than one cell. It is stored as `span: [w, h]` on the tile (absent for the
ordinary 1 x 1) and sent as the firmware's `span: {"right": w, "down": h}` only when larger.

- **Anchored at the top-left cell.** The tile's number is that cell's row-major position, so a 2 x 2
  at position 1 covers 1, 2, 4 and 5 on a 3-wide grid. Touch events report the anchor position, so
  nothing about event handling changes.
- **The firmware clips an overflow but does not reject overlaps** - two tiles asked to cover the same
  cell just stack. `grid.py` is therefore the only thing preventing one tile hiding another, and every
  position and size the dialogs offer goes through it. A large tile blocks every cell it covers.
- **A checkbox, not a step for everyone.** The details form has **Make this tile larger**. Ticked, the
  next step lists the sizes that fit at the chosen position (a form cannot change one field's options
  from another, and an extra step for every tile added would tax the common case). If nothing larger
  fits, the form comes back with an error rather than silently doing nothing.
- **Sizes offered:** every rectangle that fits, the common ones (2 x 1, 1 x 2, 2 x 2) first, the rest by
  area, and **Full screen** (the whole grid) last. Full screen appears only when the screen is otherwise
  empty, which is a consequence of the fit rule rather than a special case; the size step says so when
  it is missing. It is never auto-cleared.
- **Editing:** the checkbox starts ticked for a tile that is already large; unticking returns it to one
  cell. Moving a large tile to a place where its size does not fit says so and picks a smaller one
  instead of shrinking it silently. The tile chooser shows a tile's size (`S1·T1 2x2 [transport] ...`).
- **Experimental styles.** Only `button`, `indicator` and `buttonPrevNext` tiles are known to draw
  sensibly when larger. Sliders, dropdowns, thermostats and the rest still work but their sizes are
  labelled experimental until someone has looked at them on hardware.
- **Not solved by sizing:** the firmware draws icons and text at a fixed size, so a big tile is a bigger
  target and a bigger background image, not bigger controls. Album art scaling is a separate step.

### A.10 Album art on big tiles

A transport tile larger than 1 x 1 shows its cover the same way, but sized for the bigger tile.

**How big a tile really is** (from the firmware: `classScreen::_makeScreenLayout`,
`classTile::_tileWidth`). A cell is the screen width divided by the columns wide and the screen height
less a 33 px footer divided by the rows tall, both integer divisions; a tile is its cells less 5 px of
padding on every side. **Tiles are not square**, and the 300 and 460 px figures used earlier while
sketching this were guesses and wrong.

| Board | Grid | Cell | 1 x 1 | 2 x 2 | Full screen |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WT32S3-86S / 86V (480 x 480) | 3 x 3 | 160 x 149 | 150 x 139 | 310 x 288 | 470 x 437 |
| WT32-SC01 / SC01-PLUS (320 x 480) | 2 x 3 | 160 x 149 | 150 x 139 | 310 x 288 | 310 x 437 |

- **The panel does the enlarging.** `backgroundImage` takes a `zoom` of 50-200 % and centres the image, so
  the uploaded image is `tile pixels / zoom`, capped at a configurable longest edge (default 150 px).
  Bytes and decoded size therefore stay what they are for a 1 x 1 tile however big the tile gets, which is
  what removed the need to hold big-tile art back on the older SC01 boards.
- **Proportions follow the tile.** The cover is centre-cropped to the tile's own aspect, so a wide tile gets
  a wide image and nothing is stretched.
- **A margin when the cap bites.** On a large tile the capped image is smaller than the tile, so the
  tile's background colour is set to the cover's edge colour (`backgroundColorRgb` in the tile command,
  which the firmware accepts at runtime) and the margin reads as part of the picture. When the art goes
  away it is put back to the tile's own colour, or to black, which means "inherit the screen's".
- **Title and artist are drawn into the picture** on tiles of at least 2 x 2, on a dark band, because
  firmware text is a fixed small size. Off with the setting, and skipped if Pillow has no scalable font.
  **Pillow's built-in font is close to ASCII-only** (measured: no accented letters, no en/em dash, no euro
  sign; curly quotes and the ellipsis are fine), which would draw empty boxes for most Portuguese, French
  or Spanish titles. So an undrawable character is swapped for a plain equivalent (`Radio` for the accented
  spelling, `-` for a dash, `EUR` for the euro), a lone combining mark is dropped, and anything still
  undrawable (CJK, emoji) becomes `?`. Bundling a font would render them properly; that is left as a
  decision because it adds a binary file to the integration.
  On such a tile the transport tile's own sub-label is blanked, unless the user chose a sub-label source.
- **One image per player and size** (`art-hifi` for 1 x 1, `art-hifi-2x2` for 2 x 2) because the same
  player can be shown on tiles of different sizes. A title change re-encodes, since it is in the picture.
- **Settings, not constants**, in *Album art settings*: byte budget, zoom (100-200), largest image edge
  (60-460) and the text switch. The edge is the safety limit: album art is re-sent on every reconnect, so
  an image too big for a board would crash it again each time.
- **A 1 x 1 tile is unchanged**, byte for byte.

### A.4 Icons

The 65 icons generated for this catalog ship inside the integration as
`oxrs_touchpanel/bundled_icons.json`, so there is nothing to upload.

- **Seeding.** On startup the shared library adds any bundled icon it lacks,
  under the category recorded in the JSON, and refreshes one whose artwork
  changed in an update. It runs once per HA start, behind a lock, because
  panels set up concurrently. Panels pay nothing: only icons their tiles use
  are sent to them.
- **Ownership.** A user upload with a bundled icon's name replaces it, and
  seeding leaves it alone — the panel addresses icons by name, so two entries
  under one name was always a bug. Deleting a bundled icon is remembered in
  `.storage` (`dismissed_bundled_icons`) so it stays deleted across restarts;
  deleting the user's replacement lets the bundled one come back.
- **Suggestions.** `suggested_icons` puts a tile type's icons first in the
  picker, labelled "Suggested:", and the first *available* one is the default.
  Where a bundled icon adds a state swap (doors, presence, blinds) it leads;
  elsewhere the familiar built-in stays first and the bundled ones follow.
  Custom icons below are sorted by category so each reads as a block.
- **State pairs.** `ICON_STATE_PAIRS` lists icons that picture two states of one
  thing. A tile configured with either half shows the half matching its
  on/off `state`, for any tile type that reports one. Both halves are sent to
  the panel. If either half has been deleted, the tile keeps its configured
  icon rather than naming one the panel never received.

Filtering belongs in the registry, and reaches the picker two ways:

1. **Declaratively**, where `EntitySelectorConfig` can express it — `domain` and
   `device_class` are passed straight through.
2. **Resolved from live state**, where it can't. `eligible_entity_ids` in
   `tiles.py` walks `hass.states`, applies the tile type's `color_modes`,
   `features` and `numeric_state` rules, drops unavailable entities, and hands
   the result to the selector as `include_entities`.

Everything attribute-shaped goes through route 2, including feature bitmasks
that `EntitySelectorConfig` could filter natively. One mechanism, one place to
look, and each check is scoped to the domain it applies to so multi-domain tile
types don't lose one domain to another's rule.

The fallback matters: when nothing qualifies, `include_entities` is omitted and
the plain domain picker is shown with a logged warning, so a user with (say) no
colour bulbs sees every light rather than an empty dropdown.

A `_get_entity_filter_for_tile_type` method in `config_flow.py` used to build
filter callables per tile type. It was dead code — defined, never called — left
behind when commit `45b26d9` reverted submit-time capability validation, and has
since been removed. `include_entities` is what that code was missing: it narrows
the picker up front, so nothing has to be rejected on submit.

**What each type requires.** Colour-mode sets (`COLOR_CAPABLE_MODES`,
`BRIGHTNESS_CAPABLE_MODES`) deliberately exclude `onoff` and `unknown`.

| Type | Requires | Keeps out |
| :--- | :--- | :--- |
| `rgbw` | any of hs, xy, rgb, rgbw, rgbww | dimmer-only and on/off bulbs |
| `cct` | color_temp | colour bulbs with no tunable white |
| `slider` | any mode that dims | on/off relays |
| `updown` | cover OPEN or CLOSE | tilt-only covers |
| `updownlevel` | cover SET_POSITION, or a dimmable light | blinds that only know open/closed |
| `thermostat` | climate TARGET_TEMPERATURE | range-only (separate heat/cool setpoints) |
| `volume` | media VOLUME_STEP or VOLUME_SET | players with no volume control |
| `select` | media SELECT_SOURCE (helpers exempt) | players with no source list |
| `indicator` | numeric state | text sensors — those belong on `text` |
| `door_window`, `presence` | device_class (declarative) | unrelated binary sensors |

Notes on the judgement calls:

- **`rgbw` accepts any colour-capable light, not just RGBW.** HA converts colour
  between modes on `light.turn_on`, so an hs bulb works fine on a colour wheel.
  Tighten `color_modes` to `["rgbw"]` if you'd rather be strict.
- **`volume` accepts VOLUME_SET.** HA's default `volume_up`/`volume_down` step
  the level for any player that can set it outright.
- **`updown` doesn't require STOP.** Stop is only sent on hold, and a cover
  without it is still perfectly usable open/closed.
- **`indicator` keeps sensors reading `unknown`** when they declare a unit or
  state class, so a thermometer doesn't vanish between readings.
- Feature checks read `supported_features` from live state, so an integration
  that under-reports will have entities quietly filtered out. The all-or-nothing
  fallback won't catch that — it only fires when *nothing* qualifies.

**Verified against HA core-2026.9.2.** `CoverEntityFeature` now lives in
`cover/const.py` but is still re-exported from the package, so the import in
`tiles.py` holds. These names do churn — `ClimateEntityFeature.AUX_HEAT` was
removed between 2024.6 and now — so re-check them when bumping the HA baseline.
Note also that `EntitySelectorConfig` now treats top-level `domain` and
`device_class` as legacy, with new filter options going under a `filter` key;
the legacy form still works and is what this integration uses.

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
| Sensor | `indicator` / `button`+`text` | sensor | ✅ built (`indicator`, `text`) |
| Doors & Windows | `button` (read-only) | binary_sensor | ✅ built (`door_window`) |
| Presence | `button` (read-only) | binary_sensor | ✅ built (`presence`) |
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
| Media | `buttonPrevNext`, `dropDown`, `remote`, … | media_player | ✅ transport + playlists built; rest open |
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

### 9. Sensor — ✅ built
**Numeric:** `indicator` (pre-existing) · **Text:** `text`, style `button`

- The `indicator` style restricts `number.value` to `0-9 + - . :`, which is why
  `_format_indicator_value` strips everything else. The `text` type is the
  escape hatch: `button` style with `text` set to the raw state, so "Running"
  or "Disconnected" render fine. Non-empty text hides the icon; empty restores
  it, which is the fallback when the entity is missing.
- Accepts `sensor` and `binary_sensor` — a binary_sensor reading as literal
  `on`/`off` text is sometimes what you want over a lit tile.
- **Not done:** colouring via `"#RRGGBB {state}#"`, and duration/timestamp
  formatting as `"H:MM"`. Both are additive later.

### 10. Doors & Windows — ✅ built as `door_window`
**Style** `button`, read-only · **Domain** `binary_sensor`

- **Filter:** `device_class: ["door", "window", "garage_door", "opening"]` in the
  registry entry, passed to `EntitySelectorConfig`.
- **build_state:** `state` = `"on"` when open, `subLabel` = `"Open"` / `"Closed"`.
  A subLabel source picked in the config flow still overrides this, since
  `_augment_tile_state` runs after `build_state`.
- **handle_event:** `_display_only_handle_event` — the button style *does*
  receive taps, unlike `indicator`, so the no-op is what keeps it read-only.
- **Icon swapping — ✅ now default.** The firmware built-ins have no
  open/closed *pair* (one `_door`, one `_window`), which is why this was
  dropped at first. With the bundled icons shipped, the type defaults to
  `door-closed` and shows `door-open` while open; `window-*`, `garage` and
  `gate` pairs are suggested too. See A.4. Picking `_door` opts out.
- **Dropped from v1 — `iconColorRgb`.** Whether the firmware tints a *custom*
  PNG or only its own built-ins is undocumented, and setting it overrides the
  panel's configured on-colour with no clean way back. Needs testing on real
  hardware before shipping.

### 11. Presence — ✅ built as `presence`
Same as `door_window` with
`device_class: ["motion", "occupancy", "presence"]` and
`subLabel` = `"Detected"` / `"Clear"`. The built-ins have no person or motion
glyph, so the type defaults to the bundled `motion` icon, which swaps to
`motion-off` when clear; `presence-home` / `presence-away` is the other pair.

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
- **The 4 KB limit is softer than the docs claim** — see "Image size budget"
  below. Needs Pillow for the resize, which HA already ships.
- Treat as a "latest snapshot" tile refreshed on a slow interval, not a stream.

### Image size budget (measured, not documented)

The OXRS docs say an encoded image "should not exceed 4KB to avoid crashes -
TBC", and `library.py` enforced that. It buys about 12 colours at tile size, so
photographs come out badly posterised. Measured against a panel instead:

| Encoded size | 140px result | Outcome |
| ---: | :--- | :--- |
| 3.7 KB | 3 colours | draws, unusable for photos |
| 8 KB | 8 colours | draws |
| 16.3 KB | 58 colours | draws, "decent" |
| 25.7 KB | 256 colours | draws, indistinguishable from the original |
| 58.4 KB | full colour | draws |
| 48.6 KB | 300px (2x2 tile) | draws |
| 63.8 KB | 460px (full screen) | draws |

So `MAX_ENCODED_SIZE` is now a warning threshold, with a hard refusal at 64 KB.

**⚠️ The table above was run on the PC emulator and is wrong for real hardware.**
The emulator has neither the ESP32's RAM nor its MQTT buffer. Re-measured on a
physical TP32 (2026-09-20), budgeting the **whole JSON payload** rather than the
base64 string:

| JSON payload | 140px image | Outcome |
| ---: | :--- | :--- |
| 12,240 B | 24 colours | **drew — largest accepted** |
| 15,750 B | 64 colours | failed |
| 16,384 B | 58 colours | failed |

The wall is somewhere between 12,240 and 15,750 bytes; the exact figure was not
worth the round trips. Every emulator row above ~12 KB is therefore unreachable,
which rules out 256-colour tile art (25.7 KB), 300px art on a 2x2 tile (48.6 KB)
and full-screen art (63.8 KB) alike.

Budget the **payload**, not the base64: the JSON wrapper adds a variable number
of bytes, and budgeting the inner string is what made the boundary hard to read
the first time round. `DEFAULT_ALBUM_ART_BUDGET` sits just under the proven-good
payload and is a per-panel setting, since other firmware builds may differ.

PNG costs rise steeply with pixel size: at 48 KB a 300px image affords only 30
colours, and at 64 KB a 460px one just 11. A good-looking full-screen image
would need well over 100 KB. JPEG would change that entirely, but the docs
require PNG for backgrounds and the panel appears to agree.

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
| Play / Pause + Prev / Next | `buttonPrevNext` | ✅ built as `transport` — see below |
| Playlists | `dropDown` | ✅ built as `playlists` — see below |
| Volume (step) | `buttonUpDown` | existing `volume` type, unchanged |
| Volume (slider) | `buttonSlider` | `level` = `volume_level * 100` → `volume_set` |
| Track position | any + `level` | `level` = `media_position / media_duration`; `subLabel` = `"1:23 / 3:45"` |
| Now playing | `button` + `text` | `text` = title, `subLabel` = artist |
| Cover art | any + `backgroundImage` | ✅ built as the `album_art` option on `transport` — see below |
| Source / content | `dropDown` | existing `select` type already covers `source_list` |
| Remote (d-pad) | `remote` | opens the firmware remote screen |

**`transport`** — one tile for the whole queue. Tapping the tile body sends
`media_play_pause`; the arrows send `media_previous_track` /
`media_next_track`. Single taps only, so holding an arrow can't skip through the
queue. The tile lights while playing and shows `media_title` as its subLabel.
With the built-in `_play` or `_pause` icon it shows what a tap will do (pause
while playing); any other icon is left alone. The picker offers players with
NEXT_TRACK or PREVIOUS_TRACK.

**`album_art`** — an option on `transport` rather than a tile type of its own,
so one tile does artwork, title and controls together. `albumart.py` fetches the
cover, centre-crops it square, resizes to 140px, blurs very slightly (fine grain
is what PNG cannot compress) and binary-searches the largest palette whose whole
JSON payload fits the budget. If even two colours will not fit at 140px it
retries smaller before giving up.

- **The artwork costs the icon.** A background image only shows while the tile's
  text is non-empty, and non-empty text hides the icon — so a tile showing art
  cannot also show `_play`/`_pause`. The title stays as the subLabel. This was a
  deliberate trade against spending a second tile on a standalone art tile.
- **One fixed image name per player** (`art-<object_id>`). Re-uploading an
  existing name updates every tile using it, so a track change needs only the
  addImage — no follow-up tile command.
- **Change detection is the picture URL, not the title.** `entity_picture_local`
  carries a per-track cache token, so comparing it avoids re-encoding on every
  position update and still catches two tracks sharing a title.
- **`entity_picture_local` is preferred over `entity_picture`** because it is
  served by HA's own media_player proxy and therefore works for any player, not
  only Music Assistant.
- **Losing artwork clears the tile explicitly.** The panel keeps whatever image
  it was last given, so an idle player sends `backgroundImage: {}` with empty
  text, restoring the icon, rather than leaving a stale cover on screen.
- **The download reads to the end of the stream in chunks.** aiohttp's `read(n)` returns
  what is buffered so far, not the whole body, so any cover arriving in more than one
  chunk came back truncated and Pillow refused it (`image file is truncated`). It is now
  `iter_chunked` with the 4 MB cap enforced as data arrives, which also covers a response
  with no `Content-Length`. (Fixed in v1.11.0; every cover had been failing before that.)
- **Everything is best-effort.** A failed fetch, a missing Pillow or an
  impossible budget leaves a working transport tile with its icon intact.

**`playlists`** — a `dropDown` of up to `MAX_PLAYLISTS` (6) Music Assistant
playlists. When the tile is added, a config-flow step calls
`music_assistant.get_library` (`media_type: playlist`, ordered by name) using the
chosen player's config entry, and the user ticks which to list. Picking one on
the panel calls `music_assistant.play_media` with `enqueue: replace`. The type is
only offered while Music Assistant is loaded, and its picker is limited to
Music Assistant players via the selector's `integration` filter.

- **Target the Music Assistant entity, not a native player entity** (e.g. the
  BluOS integration's). Music Assistant owns the queue; skipping on the device's
  own entity bypasses it.
- **The playlist tile can't read back what's playing.** Music Assistant reports
  the current track, not the playlist it came from, so the tile remembers the
  last playlist it started per player (in `hass.data`), which resets when HA
  restarts.
- **The playlist list is fixed when the tile is added.** A new playlist appears
  after the tile is removed and re-added — there is no edit-tile flow.
- **`buttonPrevNext`** is purpose-built for this — the firmware even documents it
  with a `_music` icon and "Skip track" label. Its `prev` / `next` event names
  are confirmed working, as is the playlist dropdown (tested on the emulator).
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

1. ~~**Read-only cards first** — Doors & Windows, Presence, text Sensor.~~
   ✅ Done: `door_window`, `presence`, `text`. Added `device_class` to the
   registry as the picker-narrowing mechanism, which every later card reuses.
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

### The OXRS panels page (v2.0.0)

A sidebar page, **OXRS panels** (admin only), that draws every screen of every panel the
way the panel shows it, and adds, edits and removes tiles. It replaces the v1.14.5 grid
view, which was removed.

- **Files.** `editor.py` (the page's registration and websocket commands), `drafts.py`
  (checking a draft before it is saved), `frontend/editor.js` (the page: a plain web
  component, no build step, no outside libraries) and `frontend/icons/` (the firmware's
  18 built-in icons and its six control arrows, decoded from its LVGL image arrays in
  `src/resources/ios_*.c`, 32-bit section, B G R A). `_thermostat` has no file: the
  firmware's is a 1 x 1 blank the tile draws its dial over.
- **Registration.** On the first panel's setup: a static path `/oxrs_touchpanel_static`
  serving `frontend/` (uncached; the module URL carries the integration version instead),
  the websocket commands, and `panel_custom.async_register_panel` at `/oxrs-panels`. The
  sidebar entry goes when the last panel is DELETED (`async_remove_entry`), not on unload:
  every options save reloads the entry, and the page would vanish from under the user. A
  failure setting the page up is logged and never stops the panel.
- **What is drawn is what is sent.** The hub builds its payloads in `build_conf(tiles)` and
  `build_tile_states(tiles)`; `async_push_config` / `async_seed_state` publish them, and the
  page's `panels` / `preview` commands return them - for the stored tiles, or a draft's. The
  MQTT the hub publishes was checked byte-identical to v1.14.4 across two boards.
- **Look, from the firmware source.** Geometry as for album art (cell = width // cols by
  (height - 33) // rows, tile = cells x span less 10 px). The tile's white light is the
  image button's own background at the off / on brightness (`255 * pct / 100`), so the
  background image and content sit above it. The icon (60 x 60, LVGL imgbtn left image) sits
  at the top-left, recoloured white, or the icon-on colour when on (every icon is
  recoloured at full opacity, so its stored colour does not matter - two are stored black).
  Label and sub-label bottom-left in the default 14 px font (sub-label at 70 %), black when
  on. `number` (indicators): 50 px value top-left, units and sub-value at 20 px. Up/down,
  previous/next and left/right controls take the right half, top and bottom quarters.
  Non-empty `text` replaces the icon (20 px, top-left). Album art is not kept after it is
  sent, so the player's own cover (`entity_picture`) stands in; library images are drawn at
  their size times the zoom, centred. The footer shows the screen label.
- **Editing.** Tap an empty cell: choose a type (as the dialog offers them), then a form.
  Tap a tile: Edit or Remove (an old action tile: Remove only). The form is the dialog's
  own: `tile_form` builds `OxrsOptionsFlow._tile_details_schema` for the panel (position
  and "larger" dropped, the background image choice added), serialized by
  `editor.serialize_schema` (each selector's own `serialize()`; NOT voluptuous_serialize,
  which HA 2026.9 no longer ships - v2.0.0 failed to load on it) and rendered with HA's `<ha-form>`
  (loaded via the card helpers if not yet defined). Sizes are chips of every size that
  fits at the cell (`fitting_sizes`, full screen only on an empty screen, "experimental"
  for untested styles). `build_tile` validates with the same schema and builds the tile
  with the dialog's `_apply_details_input`; a playlists tile asks which playlists in a
  second step. Checked: for a light with size and image, an indicator and a playlists
  tile, the page's tile equals the tile the real dialog saves.
- **Staged, then applied once.** Changes build a draft drawn as a preview; Remove has an
  Undo; nothing reaches the panel until **Apply to panel** (or **Discard**). `apply` refuses
  when the stored tiles' fingerprint changed since the page loaded them (an edit in the
  dialog meanwhile), and when `drafts.draft_problems` finds a new or changed tile the
  dialog could not have made (bad screen / position / type / entity / size) or an overlap
  involving one; untouched tiles pass as they are, so an old action tile or a tile off the
  grid survives. Saving writes only `tiles` in the options; the entry reloads and the
  panel is sent the whole config once.
- **Live.** The page re-reads when an entity a tile uses changes (at most once a second),
  keeping an open form as it is. The last slide is a new, empty screen; adding a tile
  there creates it. Screens are still named and coloured in the dialog.
- **Moving tiles (v2.1.0).** Drag with the mouse (starts after 6 px), or press and hold a
  tile for 400 ms then drag (touch: a finger that moves more than 10 px first is a swipe,
  so swiping across tiles still changes screens; once held, touchmove is cancelled so the
  page does not scroll). The tile moves by the cell it was grabbed at. A drop marker shows
  green where it fits. Or tap Move in the tile's sheet, then tap an outlined target.
  Rules (`_dropAt`): an empty place where the tile fits at its size moves it; a tile of
  the same size there swaps the two (anchor for anchor); anything else is refused with a
  reason. Each move is a staged change with Undo; the server checks the result again on
  apply (`draft_problems`).
- **Moving tiles between screens (v2.2.0).** `_dropAt(view, index, screen, anchor)` takes
  any screen, including the new one at the end. Dragging: hovering over an arrow, or past
  the frame's side, for 600 ms (`EDGE_HOVER_MS`) changes screen with the tile still held,
  and again while it stays there; redraws wait until the drop (`_renderPending`). Move
  button: move mode survives changing screens. A swap across screens trades screen and
  anchor. After a move the page follows the tile, and says so when the old screen is left
  empty (no tiles = no screen on the panel). Server side, a tile equal to a stored one
  except for `screen`/`tile` is "moved only": its placement and size-fit are checked but
  not its type, so an old action tile can move too (each stored tile accounts for one
  such move; a copy of one that stayed put is checked in full).
- **Screens on the page (v2.1.0).** Tap a screen's name: name and background colour, in
  an ha-form. As in the dialog, a blank name is not stored (the screen shows the panel's
  title) and pure black is not stored (the screen follows the panel's colour) -
  `drafts.clean_screens`. The draft carries `screen_names` / `screen_colors`; the preview
  draws them (`hub.build_conf(..., screen_names=, screen_colors=)`) and apply saves them
  with the tiles in one write. The staleness fingerprint (`drafts.layout_fingerprint`)
  covers tiles, names and colours, so a rename in the dialog meanwhile refuses the apply.
  Invalid names/colours are refused (`screens_problems`), except values unchanged from
  what is stored.
- **Panel settings on the page (v2.3.0).** "Panel settings" in the panel's info line opens
  the dialog's own "Panel display settings" form: `OxrsOptionsFlow._panel_settings_schema`
  (split out of `async_step_panel_settings`, which now calls it) via
  `editor/settings_form`, labels from the step's translations. `editor/build_settings`
  validates with the same schema and stores with the dialog's `_panel_settings_options`,
  so the page and the dialog save identically. The draft carries `settings`: the options
  in `const.SETTINGS_KEYS` (panel_settings, temperature_offset, background_color,
  icon_on_color) in their stored shape. The preview draws them
  (`hub.build_conf(..., settings=)`, which overlays them on the stored options for
  the display settings, background and icon-on colour; MQTT byte-identical without it).
  `layout_fingerprint` now covers the settings too. Apply re-runs changed settings
  through the form (`_checked_settings`) and keeps unchanged ones as stored. Tested
  against HA 2026.9.3's NumberSelector (range) and ColorRGBSelector (three bytes).
- **Album art settings on the page (v2.4.0).** The settings sheet stacks the dialog's two
  settings forms, "Panel display settings" and "Album art", with one Save; only forms
  whose answers changed are sent (`editor/build_settings` takes `inputs: {form: answers}`
  and answers `{"settings"}` or `{"errors": {form: {field: msg}}}`). The album art step is
  split like the display step (`_album_art_schema` / `_album_art_options`;
  `ALBUM_ART_PLACEHOLDERS` fills its description). `const.SETTINGS_KEYS` is now
  `DISPLAY_SETTINGS_KEYS + ALBUM_ART_SETTINGS_KEYS`, so the draft, the fingerprint and
  apply cover both; apply checks each form's part on its own (`_checked_settings`), keeping
  an unchanged part as stored.
- **The library on the page (v2.4.0).** "Library" lists the shared background images
  and custom icons (`editor/library`: pictures as data URIs, bundled icons marked, and
  `used_by` - every tile on every panel using it, an icon pair's two halves counting
  together). Add (`editor/library_add`: kind, name, data - base64 or the data: URI a
  file picker reads - and category) goes through `config_flow.async_add_library_image` /
  `async_add_library_icon`, split out of the dialog's add steps, with the dialog's error
  messages; an image whose name is taken is refused (the panel knows images by name), an
  icon replaces the one of its name as in the dialog. Delete (`editor/library_delete`,
  kind + `item_id` - not `id`, which is the websocket message's own) asks first and says
  which tiles lose the picture. Library changes are immediate, not staged: the library is
  shared, not part of one panel's options. The page then re-reads the library and panels.
- **Restart and firmware updates (v2.5.0).** The Device sheet (`editor/device`) shows the
  board, connection and firmware the panel announces in its retained adopt message
  (`firmware.version`, `network.ip`, `network.mode`, kept by `hub._note_adopt`; the
  version also goes onto the device page as sw_version). Restart (`editor/restart`) is
  the Reboot button's MQTT `{"restart": true}`. v2.5.0 took firmware from the GitHub
  releases (and had an update entity); **since v2.7.0 it comes from a file the user
  chooses**, and nothing is fetched from the internet - see below.
- **Firmware from a file (v2.7.0).** The page reads the chosen file and says at once
  whether it is the right kind (`inspectFirmware`); installing uploads it with
  `hass.fetchWithAuth` to `FirmwareUploadView` (`POST /api/oxrs_touchpanel/firmware/
  <entry_id>`, logged-in admins; HTTP rather than websocket because an image is a few MB;
  HA takes bodies up to 16 MB). The view refuses (4xx `{message, code}`) when a job runs,
  the panel is offline or has no address, or `firmware.check_image` fails: a `_FLASH`
  file name (the whole-flash USB image), size outside 64 kB-8 MB, no 0xE9, no app
  description (`esp_app_desc_t` magic 0xABCD5432 at offset 32 - a bootloader or a
  whole-flash image has none), or a chip id (offset 12: 0 = ESP32, 9 = ESP32-S3) other
  than the board's (`firmware.CHIPS`; an unknown board skips this, the panel's own
  Update still validates). Otherwise 202 and the job runs in the background: POST whole
  to `http://<ip>/api/ota` (Content-Length needed: `_postApiOta` sizes `Update.begin`
  from it; no auth by the firmware's design), then wait for the panel's next adopt
  message (`hub.expect_announce`, any version: a chosen file's version isn't known
  beforehand), reporting the version before and after; failing after 5 minutes. The
  page shows the build name whose `_OTA.bin` fits (`firmware.BUILDS`) and the build date
  read from the file. The v2.5.0 update entity is removed from the registry at setup
  (`__init__._remove_retired_entities`).
- **Found panels, deleting, favourites (v2.6.0).** Panels found on MQTT: the discovery
  flows of this domain waiting at "confirm" (`editor.discovered_panels`; the mqtt step
  now puts the board in its title placeholders) come with `editor/panels`; "Add panel"
  (`editor/add_discovered`) confirms the flow through `flow.async_configure`, as Settings
  does. Delete (`editor/delete_panel`, Device sheet, confirmed) is
  `config_entries.async_remove`, so `async_remove_entry` clears the retained MQTT
  messages as it does from the integration page. Favourites (`favourites.py`, own Store
  `oxrs_touchpanel_favourites`, shared by every panel, max 60): the tile less screen /
  tile; the same tile elsewhere is the same favourite; old action tiles can't be one.
  (v2.6.0 also offered "frequently used" setups; v2.7.2 dropped them at the user's
  request - two lists made the picker cluttered.) `editor/suggestions` returns the
  favourites with `fits` for the chosen free place (`fitting_sizes`); `editor/tile_form` takes a
  `template` for a new tile and pre-fills the serialized form with it (`_prefill`: only
  values the form offers), choosing the template's size when it fits. Submitting is the
  ordinary `build_tile`, so a placed favourite is checked like any new tile.
- **Keeping the panel across its reload (v2.7.1).** Saving a panel's options reloads its
  entry, and while it reloads it is not in `hass.data[DOMAIN]`, so `editor/panels` left
  it out. The page used to wait a fixed 1.5 s after apply and then keep "the same panel"
  by list position - on a Raspberry Pi the reload was often still running, the panel was
  missing, and the page fell back to another one and never looked again. Now
  `editor/panels` also returns `unavailable` (entries of the domain not running, with
  their state; `reloading` for setup_in_progress / not_loaded / unload_in_progress /
  setup_retry), `editor/apply` returns the fingerprint of what it saved, and the page
  keeps the panel by entry id (`_currentEntryId`, remembered in localStorage across
  page reloads). `_choosePanel` waits - "reloading with the changes…", polling every
  second for up to a minute - while the panel is missing but reloading, or still shows
  the old fingerprint after an apply; a panel that failed to start is named with its
  state instead of silently replaced.
- **The page never changes panel by itself (v2.7.2).** v2.7.1 still shifted focus in
  two ways: a panel running but whose view failed to build was in neither `panels` nor
  `unavailable`, so the page took it for deleted and moved on; and `_fetch` took on the
  new panel list before an await (the favourites read) and chose the panel after it, so
  for that moment the old index pointed at another panel. Now `unavailable` is every
  configured entry that isn't drawn (running-but-not-drawn counts as reloading), the page
  leaves its panel only when it is in neither list (deleted), keeps waiting with no
  limit (1 s polls for a minute, then 10 s), gathers both answers before taking either
  on, and ignores the answer to any read older than the latest (`_fetchSeq`). Tested by
  sampling the page every 20 ms through apply, reload and three failed view builds, with
  entity-driven reads every 0.7 s: never another panel. Also: the sidebar panel's config
  carries the version; a page left open across an update (whose browser keeps running the
  old code - the "oxrs-panel-editor has already been used" error in the log) now shows
  "reload to use the new version", and the define is guarded so it no longer throws.
  The device page's firmware version uses `async_get_device_by_identifier` where HA has
  it (`async_get_device` is deprecated, gone in 2027.8).
- **Configuring a panel when it connects (v2.7.3).** Checked against the OXRS docs and the
  firmware / OXRS-IO-MQTT-ESP32-LIB source: the panel subscribes to `conf/` and `cmnd/`,
  then publishes `{"online":true}` retained on `stat/<id>/lwt` (its will is
  `{"online":false}`), then its adopt message; it keeps no configuration, images or icons
  across a restart, and the docs have the controller send `conf/` when it sees the panel
  come online. The integration did that, but: at every entry setup (HA start, and every
  Apply, which reloads the entry) it pushed twice at once - `async_setup` pushed, and the
  retained "online" replayed for the new subscription also pushed, since `available`
  starts False - so two remove/conf/image/art/state sequences interleaved; a live "online"
  while already online (the panel back before the broker saw it go) got nothing; and tile
  states were only sent after album art, which is fetched and encoded per player (up to
  the 15 s fetch timeout each), so tiles sat without their states meanwhile. Now: a live
  "online" (retain flag clear) always pushes; a retained one pushes only if the panel may
  have missed one (`_needs_push`: set at start, on "offline" and when HA loses the broker
  - `mqtt.async_subscribe_connection_status`); setup pushes only if nothing asked yet.
  Pushes run in a background task that a newer one replaces, "offline" and unload cancel,
  and a lock keeps direct pushes (button, editor) from interleaving. Order: remove, conf,
  images and icons, every tile's state, then album art followed by the art tiles' states
  again; the art cache is cleared first so no state names an image the panel lost.
  `PANEL_MQTT_BUFFER` (16384, the library's `MQTT_MAX_MESSAGE_SIZE` on ESP32 - PubSubClient
  drops a larger packet whole, header and topic counted): conf, images, icons and album
  art over it are logged once per setup, still sent (other builds may take more - the
  album art budget stays a setting). A 99-tile conf is about 9 KB.
- **Not yet:** nothing from the dialog is left out; the dialog stays for those who prefer it.

