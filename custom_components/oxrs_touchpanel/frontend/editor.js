// OXRS panels - a sidebar page that draws each panel's screens as the panel shows them,
// and changes their tiles.
//
// Everything drawn comes from the integration's websocket commands, which return the
// same conf payload and tile states it publishes to the panel - for a draft of unsaved
// changes too - so this page cannot drift from the panel. Tile forms are built by the
// options dialog's own code and rendered with Home Assistant's own <ha-form>.
//
// Edits are staged: they change a draft (tiles, screen names and colours, the panel
// and album art settings) that is drawn as a preview, and nothing reaches the panel until "Apply to panel", which
// sends the whole draft once. The image and icon library is shared by every panel, so
// adding or deleting there takes effect at once, as in the dialog. Tiles move by dragging (mouse), press-and-hold then
// drag (touch - a plain swipe still changes screens), or the Move button - to another
// screen too: while dragging, hover over the arrows (or past the screen's side) to
// change screens; with the Move button, change screens before tapping the target.
//
// Geometry and look follow the firmware (OXRS-IO-TouchPanel-ESP32-FW):
//   cell = (screen width / cols) x ((screen height - 33 footer) / rows), integer division;
//   a tile is cells x span less 5 px padding on each side;
//   the icon (60 x 60) sits at the tile's top-left, recoloured white, or the icon-on
//   colour when the tile is on; the label and sub-label sit bottom-left in the 14 px
//   default font, black when on; a white overlay at the tile brightness (off / on %)
//   lights the tile; up/down and previous/next controls take the right half.
// Plain web component, no build step and no outside libraries.

const WS = {
  panels: "oxrs_touchpanel/editor/panels",
  preview: "oxrs_touchpanel/editor/preview",
  tileForm: "oxrs_touchpanel/editor/tile_form",
  buildTile: "oxrs_touchpanel/editor/build_tile",
  apply: "oxrs_touchpanel/editor/apply",
  settingsForm: "oxrs_touchpanel/editor/settings_form",
  buildSettings: "oxrs_touchpanel/editor/build_settings",
  library: "oxrs_touchpanel/editor/library",
  libraryAdd: "oxrs_touchpanel/editor/library_add",
  libraryDelete: "oxrs_touchpanel/editor/library_delete",
};
const BUILTIN_ICONS = new Set([
  "_3dprint", "_blind", "_bulb", "_ceilingfan", "_coffee", "_door", "_feed", "_locked",
  "_music", "_onoff", "_pause", "_play", "_remote", "_slider", "_speaker", "_thermometer",
  "_unlocked", "_window",
]);
const DEFAULT_ICON_ON = { r: 91, g: 190, b: 91 };
const CONTROLS = {
  buttonUpDown: ["up", "down"],
  buttonUpDownLevel: ["up", "down"],
  buttonSelector: ["up", "down"],
  buttonPrevNext: ["prev", "next"],
  buttonLeftRight: ["left", "right"],
};
const REFRESH_MS = 1000;
// After an apply the integration reloads the panel; give it a moment before reading back.
const APPLY_SETTLE_MS = 1500;
const UNDO_MS = 8000;
// Touch: how long a press must be held before it picks a tile up, and how far a
// finger may wander before that, which is a swipe rather than a press.
const HOLD_MS = 400;
const HOLD_SLOP_PX = 10;
// Mouse: how far it must move with the button down before a drag starts.
const DRAG_START_PX = 6;
// While dragging: how long to hover over an arrow, or past the screen's side, before
// the page changes screen with the tile still held.
const EDGE_HOVER_MS = 600;
// Sheet modes that hold a form being filled in; the data refreshing must not wipe them.
const FORM_MODES = new Set(["form", "playlists", "screen", "settings", "library"]);

// A colour the firmware treats as set: pure black means "unset, inherit".
function colour(rgb) {
  if (!rgb || typeof rgb !== "object") return null;
  const r = Number(rgb.r) || 0, g = Number(rgb.g) || 0, b = Number(rgb.b) || 0;
  if (!r && !g && !b) return null;
  return `rgb(${r}, ${g}, ${b})`;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const clone = (value) => JSON.parse(JSON.stringify(value));

// append() writes null as the text "null"; this skips empty slots, as el() does.
function add(parent, ...children) {
  parent.append(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
}

// Home Assistant loads <ha-form> on demand. A card editor that uses it is the usual way
// to make sure it is defined before a custom page needs it.
let haFormReady = null;
function ensureHaForm() {
  if (customElements.get("ha-form")) return Promise.resolve(true);
  if (!haFormReady) {
    haFormReady = (async () => {
      try {
        // Loading the card helpers defines the built-in cards; asking a card class for
        // its editor loads that editor, which brings <ha-form> with it. An entities
        // card needs at least one entity, or its config is refused.
        const helpers = await window.loadCardHelpers?.();
        await helpers?.createCardElement({ type: "entities", entities: ["sun.sun"] });
        for (const name of ["hui-entities-card", "hui-tile-card", "hui-button-card"]) {
          if (customElements.get("ha-form")) break;
          await customElements.get(name)?.getConfigElement?.();
        }
      } catch (err) {
        // Fall through to waiting; the fallback message covers a failure.
      }
      await Promise.race([
        customElements.whenDefined("ha-form"),
        new Promise((resolve) => setTimeout(resolve, 5000)),
      ]);
      return !!customElements.get("ha-form");
    })();
  }
  return haFormReady;
}

// A value as JSON with its keys sorted, to tell whether two form answers differ.
function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${stable(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

// The values a serialized form starts with: its defaults and suggested values.
function initialData(schema) {
  const data = {};
  for (const field of schema) {
    if (field.description && field.description.suggested_value !== undefined) {
      data[field.name] = field.description.suggested_value;
    } else if (field.default !== undefined) {
      data[field.name] = field.default;
    }
  }
  return data;
}

class OxrsPanelEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._panels = [];
    this._types = [];
    this._panelIndex = 0;
    this._screenIndex = 0;
    this._selected = null; // {kind: "tile"|"empty", screen, tile}
    this._sheetMode = null; // null | "types" | "form" | "playlists"
    this._form = null; // the open tile form: {spec, data, size, playlists, error, busy}
    this._draft = null; // {entry_id, base, tiles, changes}
    this._preview = null; // the panel drawn with the draft's tiles
    this._undo = null; // {tiles, changes, label, timer}
    this._message = null; // {text, error}
    this._applying = false;
    this._error = null;
    this._loading = true;
    this._fetchTimer = null;
    this._lastFetch = 0;
    this._watched = new Map();
    this._resize = new ResizeObserver(() => this._fit());
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menuButton) this._menuButton.hass = hass;
    if (this._haForm) this._haForm.hass = hass;
    if (first) this._fetch();
    else if (this._entitiesChanged()) this._scheduleFetch();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuButton) this._menuButton.narrow = narrow;
    this.toggleAttribute("narrow", !!narrow);
  }

  set panel(panel) {
    this._panelConfig = panel;
  }

  get _static() {
    return this._panelConfig?.config?.static_url || "/oxrs_touchpanel_static";
  }

  connectedCallback() {
    if (!this._built) this._build();
  }

  disconnectedCallback() {
    this._resize.disconnect();
    clearTimeout(this._fetchTimer);
  }

  // ── data ──────────────────────────────────────────────────────────────
  _entitiesChanged() {
    if (!this._hass) return false;
    for (const [entityId, stateObj] of this._watched) {
      if (this._hass.states[entityId] !== stateObj) return true;
    }
    return false;
  }

  _scheduleFetch() {
    if (this._fetchTimer) return;
    const wait = Math.max(0, REFRESH_MS - (Date.now() - this._lastFetch));
    this._fetchTimer = setTimeout(() => {
      this._fetchTimer = null;
      this._fetch();
    }, wait);
  }

  async _fetch() {
    if (!this._hass) return;
    this._lastFetch = Date.now();
    const previousId = this._stored?.entry_id;
    try {
      const result = await this._hass.callWS({ type: WS.panels });
      this._panels = result.panels || [];
      this._types = result.types || [];
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._loading = false;
    // Keep showing the same panel if it is still there.
    const index = this._panels.findIndex((p) => p.entry_id === previousId);
    this._panelIndex = index >= 0 ? index : Math.min(this._panelIndex, Math.max(0, this._panels.length - 1));
    if (this._draft && this._draft.entry_id !== this._stored?.entry_id) this._clearDraft();
    if (this._draft) await this._refreshPreview(false);
    this._watch();
    this._renderAll();
  }

  _watch() {
    this._watched = new Map();
    for (const view of [...this._panels, this._preview].filter(Boolean)) {
      for (const entityId of view.entities || []) this._watched.set(entityId, this._hass.states[entityId]);
    }
  }

  async _refreshPreview(render = true) {
    if (!this._draft) {
      this._preview = null;
      return;
    }
    try {
      this._preview = await this._hass.callWS({
        type: WS.preview,
        entry_id: this._draft.entry_id,
        tiles: this._draft.tiles,
        screen_names: this._draft.screen_names,
        screen_colors: this._draft.screen_colors,
        settings: this._draft.settings,
      });
    } catch (err) {
      this._say(`Couldn't draw the changes: ${err?.message || err}`, true);
    }
    this._watch();
    if (render) this._renderAll();
  }

  get _stored() {
    return this._panels[this._panelIndex];
  }

  // What is drawn: the draft's preview while there is one, else the stored panel.
  get _view() {
    const stored = this._stored;
    if (stored && this._draft && this._preview && this._draft.entry_id === stored.entry_id) return this._preview;
    return stored;
  }

  _screens(view) {
    return (view?.conf?.screens || []).slice().sort((a, b) => a.screen - b.screen);
  }

  // The next screen number, offered as an empty "new screen" at the end.
  _newScreenNumber(view) {
    const screens = this._screens(view);
    return screens.length ? screens[screens.length - 1].screen + 1 : 1;
  }

  // ── draft ─────────────────────────────────────────────────────────────
  _startDraft() {
    const stored = this._stored;
    if (!this._draft) {
      this._draft = {
        entry_id: stored.entry_id,
        base: stored.fingerprint,
        tiles: clone(stored.config_tiles),
        screen_names: clone(stored.config_screen_names || {}),
        screen_colors: clone(stored.config_screen_colors || {}),
        settings: clone(stored.config_settings || {}),
        changes: 0,
      };
    }
    return this._draft;
  }

  _clearDraft() {
    this._draft = null;
    this._preview = null;
    this._dropUndo();
  }

  // change: any of {tiles, screen_names, screen_colors, settings}, replacing the draft's.
  async _commit(change, label) {
    const draft = this._startDraft();
    const before = {
      tiles: clone(draft.tiles),
      screen_names: clone(draft.screen_names),
      screen_colors: clone(draft.screen_colors),
      settings: clone(draft.settings),
      changes: draft.changes,
    };
    Object.assign(draft, change);
    draft.changes += 1;
    this._moving = null;
    this._message = null; // a new change replaces the last one's message
    this._selected = null;
    this._sheetMode = null;
    this._form = null;
    this._settingsForm = null;
    await this._refreshPreview();
    if (label) this._offerUndo(before, label);
  }

  _offerUndo(before, label) {
    this._dropUndo();
    this._undo = {
      ...before,
      label,
      timer: setTimeout(() => {
        this._undo = null;
        this._renderBar();
      }, UNDO_MS),
    };
    this._renderBar();
  }

  _dropUndo() {
    if (this._undo) clearTimeout(this._undo.timer);
    this._undo = null;
  }

  async _doUndo() {
    if (!this._undo || !this._draft) return;
    this._draft.tiles = this._undo.tiles;
    this._draft.screen_names = this._undo.screen_names;
    this._draft.screen_colors = this._undo.screen_colors;
    this._draft.settings = this._undo.settings;
    this._draft.changes = this._undo.changes;
    this._dropUndo();
    if (!this._draft.changes) this._clearDraft();
    await this._refreshPreview();
    this._renderAll();
  }

  _discard() {
    this._clearDraft();
    this._selected = null;
    this._sheetMode = null;
    this._form = null;
    this._screenForm = null;
    this._settingsForm = null;
    this._libraryState = null;
    this._say(null);
    this._renderAll();
    // The stored tiles may have moved on meanwhile; show them as they are now.
    this._fetch();
  }

  async _apply() {
    if (!this._draft || this._applying) return;
    this._applying = true;
    this._renderBar();
    try {
      await this._hass.callWS({
        type: WS.apply,
        entry_id: this._draft.entry_id,
        tiles: this._draft.tiles,
        screen_names: this._draft.screen_names,
        screen_colors: this._draft.screen_colors,
        settings: this._draft.settings,
        fingerprint: this._draft.base,
      });
      this._clearDraft();
      this._selected = null;
      this._sheetMode = null;
      this._form = null;
      this._say("Sent to the panel.");
      await new Promise((resolve) => setTimeout(resolve, APPLY_SETTLE_MS));
      await this._fetch();
    } catch (err) {
      this._say(err?.message || String(err), true);
      // Re-read the stored tiles, so the page shows what changed elsewhere.
      if (err?.code === "changed_elsewhere") await this._fetch();
    }
    this._applying = false;
    this._renderBar();
  }

  _say(text, error = false) {
    this._message = text ? { text, error } : null;
    this._renderBar();
  }

  // ── layout ────────────────────────────────────────────────────────────
  _build() {
    this._built = true;
    const root = this.shadowRoot;
    root.replaceChildren();
    root.append(el("style", {}, STYLE));
    this._menuButton = document.createElement("ha-menu-button");
    this._menuButton.hass = this._hass;
    this._menuButton.narrow = this._narrow;
    this._panelSelect = el("select", {
      "aria-label": "Panel",
      onchange: (e) => this._switchPanel(Number(e.target.value)),
    });
    this._stageEl = el("div", { class: "stage" });
    this._sheetEl = el("div", { class: "sheet" });
    this._barEl = el("div", { class: "bar" });
    root.append(
      el("div", { class: "toolbar" }, this._menuButton, el("div", { class: "title" }, "OXRS panels"), this._panelSelect),
      el("div", { class: "main" }, this._stageEl, this._sheetEl),
      this._barEl
    );
    this._renderAll();
  }

  _switchPanel(index) {
    if (this._draft && this._draft.changes) {
      // A draft belongs to one panel; switching away would lose it silently.
      this._panelSelect.value = String(this._panelIndex);
      this._say("Apply or discard the changes to this panel first.", true);
      return;
    }
    this._clearDraft();
    this._panelIndex = index;
    this._screenIndex = 0;
    this._selected = null;
    this._sheetMode = null;
    this._form = null;
    this._screenForm = null;
    this._settingsForm = null;
    this._libraryState = null;
    this._renderAll();
  }

  _renderAll() {
    if (!this._built) return;
    if (this._drag?.active) {
      this._renderPending = true; // drawn when the tile is dropped
      return;
    }
    this._renderToolbar();
    this._renderStage();
    // A form being filled in is left alone; everything else follows the data.
    if (!FORM_MODES.has(this._sheetMode)) this._renderSheet();
    this._renderBar();
  }

  _renderToolbar() {
    const select = this._panelSelect;
    select.replaceChildren(
      ...this._panels.map((p, i) => el("option", { value: i, selected: i === this._panelIndex }, p.title))
    );
    select.style.display = this._panels.length > 1 ? "" : "none";
  }

  _renderStage() {
    const stage = this._stageEl;
    const scroll = this._track ? this._track.scrollLeft : 0;
    stage.replaceChildren();
    if (this._loading) return stage.append(el("div", { class: "message" }, "Loading panels…"));
    if (this._error) return stage.append(el("div", { class: "message error" }, `Couldn't load the panels: ${this._error}`));
    const view = this._view;
    if (!view) return stage.append(el("div", { class: "message" }, "No OXRS panel is set up and running."));

    const screens = this._screens(view);
    const slides = [...screens, { screen: this._newScreenNumber(view), label: "", tiles: [], isNew: true }];
    if (this._screenIndex >= slides.length) this._screenIndex = slides.length - 1;

    const grid = view.grid;
    stage.append(el(
      "div",
      { class: "info" },
      el("span", { class: "name" }, view.title),
      el("span", {}, `${view.hardware || "board not reported"} · ${grid.cols} × ${grid.rows} tiles`),
      view.available ? null : el("span", { class: "offline" }, "offline"),
      el(
        "span",
        { class: "info-actions" },
        el("button", { class: "settings-button", onclick: () => this._openSettings() }, "Panel settings"),
        el("button", { class: "settings-button", onclick: () => this._openLibrary() }, "Library")
      )
    ));

    const track = el("div", { class: "track" });
    track.addEventListener("scroll", () => this._onScroll(track), { passive: true });
    for (const screen of slides) track.append(this._slide(view, screen));
    stage.append(track);

    stage.append(el(
      "div",
      { class: "nav" },
      el("button", { class: "arrow", "aria-label": "Previous screen", onclick: () => this._go(-1) }, "‹"),
      el(
        "div",
        { class: "dots" },
        slides.map((s, i) =>
          el("button", {
            class: `${i === this._screenIndex ? "dot on" : "dot"}${s.isNew ? " new" : ""}`,
            "aria-label": s.isNew ? "New screen" : `Screen ${s.screen}`,
            onclick: () => this._goTo(i),
          })
        )
      ),
      el("button", { class: "arrow", "aria-label": "Next screen", onclick: () => this._go(1) }, "›")
    ));

    this._track = track;
    this._slides = slides;
    if (this._moving) this._markDropTargets();
    this._resize.disconnect();
    this._resize.observe(track);
    this._fit();
    track.scrollLeft = scroll || this._screenIndex * track.clientWidth;
    requestAnimationFrame(() => {
      this._fit();
      track.scrollLeft = this._screenIndex * track.clientWidth;
    });
  }

  _go(step) {
    const count = this._slides?.length || 0;
    if (!count) return;
    this._goTo((this._screenIndex + step + count) % count);
  }

  _goTo(index) {
    this._screenIndex = index;
    this._selectNone();
    this._track?.scrollTo({ left: index * this._track.clientWidth, behavior: "smooth" });
    this._updateDots();
  }

  _onScroll(track) {
    const index = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
    if (index !== this._screenIndex && index < (this._slides?.length || 0)) {
      this._screenIndex = index;
      this._selectNone();
      this._updateDots();
    }
  }

  _selectNone() {
    // Keep an open form, and a move in progress: its target may be on another screen.
    if (FORM_MODES.has(this._sheetMode) || this._sheetMode === "move") return;
    this._selected = null;
    this._sheetMode = null;
    this._markSelected();
    this._renderSheet();
  }

  _updateDots() {
    this.shadowRoot.querySelectorAll(".dot").forEach((dot, i) => dot.classList.toggle("on", i === this._screenIndex));
  }

  // Scale every screen, drawn at the panel's own pixel size, to the width available.
  _fit() {
    const track = this._track;
    const view = this._view;
    if (!track || !view) return;
    const { width, height } = view.screen_px;
    const available = Math.min(track.clientWidth - 16, 560);
    if (available <= 0) return;
    const scale = Math.max(0.3, available / width);
    track.querySelectorAll(".frame").forEach((frame) => {
      frame.style.width = `${width * scale}px`;
      frame.style.height = `${height * scale}px`;
      frame.firstElementChild.style.transform = `scale(${scale})`;
    });
  }

  // ── drawing a screen ──────────────────────────────────────────────────
  _slide(view, screen) {
    const { width, height } = view.screen_px;
    const cols = view.grid.cols, rows = view.grid.rows;
    const footer = view.footer_px, pad = view.padding_px;
    const cellW = Math.floor(width / cols);
    const cellH = Math.floor((height - footer) / rows);
    const bg = colour(screen.backgroundColorRgb) || colour(view.conf.backgroundColorRgb) || "#000";

    const canvas = el("div", { class: "screen", style: { width: `${width}px`, height: `${height}px`, background: bg } });
    const covered = new Set();
    for (const tile of screen.tiles || []) {
      const w = tile.span?.right || 1, h = tile.span?.down || 1;
      const row = Math.floor((tile.tile - 1) / cols), col = (tile.tile - 1) % cols;
      if (row >= rows) continue;
      for (let dy = 0; dy < h; dy++) for (let dx = 0; dx < w; dx++) covered.add((row + dy) * cols + col + dx + 1);
      canvas.append(this._tile(view, screen, tile, {
        left: col * cellW + pad,
        top: row * cellH + pad,
        width: cellW * w - 2 * pad,
        height: cellH * h - 2 * pad,
      }));
    }
    for (let position = 1; position <= cols * rows; position++) {
      if (covered.has(position)) continue;
      const row = Math.floor((position - 1) / cols), col = (position - 1) % cols;
      canvas.append(el(
        "button",
        {
          class: this._isSelected("empty", screen.screen, position) ? "empty selected" : "empty",
          style: { left: `${col * cellW + pad}px`, top: `${row * cellH + pad}px`, width: `${cellW - 2 * pad}px`, height: `${cellH - 2 * pad}px` },
          "aria-label": `Empty position ${position}`,
          "data-screen": String(screen.screen),
          "data-tile": String(position),
          onclick: () => this._pick({ kind: "empty", screen: screen.screen, tile: position }),
        },
        el("span", { class: "pos" }, String(position)),
        el("span", { class: "plus" }, "+")
      ));
    }
    canvas.append(el(
      "div",
      { class: "footer", style: { height: `${footer}px` } },
      el("span", { class: "footer-icon" }, "⌂"),
      el("span", { class: "footer-label" }, screen.isNew ? "" : screen.label || ""),
      el("span", { class: "footer-icon" }, "⚙")
    ));
    return el(
      "div",
      { class: "slide" },
      screen.isNew
        ? el(
            "div",
            { class: "screen-name" },
            "New screen",
            el("span", {}, ` · add a tile to create screen ${screen.screen}`)
          )
        : el(
            "button",
            {
              class: "screen-name editable",
              title: "Change this screen's name or colour",
              onclick: () => this._openScreen(screen.screen),
            },
            screen.label || `Screen ${screen.screen}`,
            el("span", {}, ` · screen ${screen.screen} ✎`)
          ),
      el("div", { class: "frame" }, canvas)
    );
  }

  _isSelected(kind, screen, tile) {
    const s = this._selected;
    return !!s && s.kind === kind && s.screen === screen && s.tile === tile;
  }

  _state(view, screen, tile) {
    return (view.states || []).find((s) => s.screen === screen && s.tile === tile) || {};
  }

  _tile(view, screen, tileConf, box) {
    const state = this._state(view, screen.screen, tileConf.tile);
    const on = state.state === "on";
    const conf = view.conf;
    const iconColour = colour(conf.iconOnColorRgb) || colour(DEFAULT_ICON_ON);
    const fg = on ? iconColour : "#fff";
    const textColour = on ? "#000" : "#fff";
    const brightness = on ? conf.tileBrightnessOn ?? 100 : conf.tileBrightnessOff ?? 10;

    const node = el("button", {
      class: this._isSelected("tile", screen.screen, tileConf.tile) ? "tile selected" : "tile",
      style: { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` },
      "aria-label": tileConf.label || `Tile ${tileConf.tile}`,
      "data-screen": String(screen.screen),
      "data-tile": String(tileConf.tile),
      onclick: () => this._pick({ kind: "tile", screen: screen.screen, tile: tileConf.tile }),
    });

    // Own colour (a later state command wins over the config), else the screen shows through.
    const own = colour(state.backgroundColorRgb) || colour(tileConf.backgroundColorRgb);
    if (own) node.append(el("div", { class: "layer", style: { background: own } }));

    // The tile's white light, its brightness in the off or on state. It is the button's
    // own background in the firmware, so the background image and content sit above it.
    node.append(el("div", { class: "layer", style: { background: "#fff", opacity: String(brightness / 100) } }));

    // Album art is not kept after it is sent, so the player's cover stands in for it;
    // a library image is drawn at its own size times the zoom, centred.
    const artUrl = view.art?.[`${screen.screen}/${tileConf.tile}`];
    const image = state.backgroundImage;
    if (artUrl) {
      node.append(el("div", { class: "layer clip" }, el("img", { class: "art", src: artUrl, alt: "" })));
    } else if (image && image.name && view.images?.[image.name]) {
      const img = el("img", { class: "bgimg", src: view.images[image.name], alt: "" });
      img.style.transform = `translate(-50%, -50%) scale(${(Number(image.zoom) || 100) / 100})`;
      node.append(el("div", { class: "layer clip" }, img));
    }

    const number = state.number;
    const text = typeof state.text === "string" ? state.text : "";
    if (number && (number.value || number.units || number.subValue || number.subUnits)) {
      node.append(el(
        "div",
        { class: "number", style: { color: on ? iconColour : "#fff" } },
        el("span", { class: "value" }, number.value ?? ""),
        el("span", { class: "units" }, number.units ?? ""),
        el("div", { class: "subvalue", style: { color: textColour } }, `${number.subValue ?? ""} ${number.subUnits ?? ""}`.trim())
      ));
    } else if (text || artUrl) {
      // Non-empty text replaces the icon; a single space hides it (album art tiles).
      if (text.trim()) node.append(el("div", { class: "icontext", style: { color: on ? iconColour : "#fff" } }, text));
    } else {
      const iconName = state.icon || tileConf.icon;
      const src = BUILTIN_ICONS.has(iconName) ? `${this._static}/icons/${iconName}.png` : view.icons?.[iconName];
      if (src) {
        node.append(el("div", { class: "icon", style: { background: fg, maskImage: `url("${src}")`, webkitMaskImage: `url("${src}")` } }));
      }
    }

    const controls = CONTROLS[tileConf.style];
    if (controls) {
      for (const [i, name] of controls.entries()) {
        const src = `${this._static}/icons/${name}.png`;
        node.append(el("div", {
          class: "control",
          style: { top: i === 0 ? "0" : "50%", background: fg, maskImage: `url("${src}")`, webkitMaskImage: `url("${src}")` },
        }));
      }
    }

    const label = state.label ?? tileConf.label;
    if (label) node.append(el("div", { class: "label", style: { color: textColour } }, label));
    if (state.subLabel) node.append(el("div", { class: "sublabel", style: { color: textColour } }, state.subLabel));
    this._enableDrag(node, screen.screen, tileConf.tile);
    return node;
  }

  // ── moving tiles ──────────────────────────────────────────────────────
  _geometry(view) {
    const { width, height } = view.screen_px;
    const { cols, rows } = view.grid;
    return {
      cols,
      rows,
      pad: view.padding_px,
      cellW: Math.floor(width / cols),
      cellH: Math.floor((height - view.footer_px) / rows),
    };
  }

  // The cells a tile of w x h covers from an anchor, or null if it would leave the grid.
  _cells(view, anchor, w, h) {
    const { cols, rows } = view.grid;
    if (!Number.isInteger(anchor) || anchor < 1 || anchor > cols * rows) return null;
    const row = Math.floor((anchor - 1) / cols), col = (anchor - 1) % cols;
    if (row + h > rows || col + w > cols) return null;
    const cells = [];
    for (let dy = 0; dy < h; dy++) for (let dx = 0; dx < w; dx++) cells.push((row + dy) * cols + col + dx + 1);
    return cells;
  }

  // The tiles on a screen, at the size they are drawn (clipped to the grid).
  _placed(view, screen) {
    const { cols, rows } = view.grid;
    return (view.tiles || [])
      .filter((t) => t.screen === screen && Number.isInteger(t.tile) && t.tile >= 1 && t.tile <= cols * rows)
      .map((t) => {
        const row = Math.floor((t.tile - 1) / cols), col = (t.tile - 1) % cols;
        return {
          index: t.index,
          anchor: t.tile,
          w: Math.min(t.size[0], cols - col),
          h: Math.min(t.size[1], rows - row),
          label: t.label || t.type_label,
        };
      });
  }

  // The tile at `index`, at the size it is drawn, with its screen.
  _placedTile(view, index) {
    const meta = (view.tiles || []).find((t) => t.index === index);
    if (!meta) return null;
    const me = this._placed(view, meta.screen).find((p) => p.index === index);
    return me ? { ...me, screen: meta.screen } : null;
  }

  // What putting a tile's top-left corner at `anchor` on `screen` would do: move it,
  // swap it with a tile of the same size there, or nothing (with the reason). The
  // screen may be another one, or the new one at the end.
  _dropAt(view, index, screen, anchor) {
    const me = this._placedTile(view, index);
    if (!me) return { ok: false, reason: "That tile can't be moved." };
    if (screen === me.screen && anchor === me.anchor) return { ok: false, same: true };
    const placed = this._placed(view, screen);
    const other = placed.find((p) => p.anchor === anchor && p.index !== index);
    if (other) {
      if (other.w === me.w && other.h === me.h) return { ok: true, swap: other, me };
      return { ok: false, reason: `${other.label} is a different size, so they can't swap.` };
    }
    const cells = this._cells(view, anchor, me.w, me.h);
    if (!cells) return { ok: false, reason: `${me.label} doesn't fit there at its size.` };
    const taken = new Set();
    for (const p of placed) {
      if (p.index === index) continue;
      for (const c of this._cells(view, p.anchor, p.w, p.h) || []) taken.add(c);
    }
    if (cells.some((c) => taken.has(c))) return { ok: false, reason: "Another tile is in the way." };
    return { ok: true, me };
  }

  async _move(index, screen, anchor) {
    const view = this._view;
    const result = this._dropAt(view, index, screen, anchor);
    if (!result.ok) {
      if (!result.same) this._say(result.reason, true);
      return;
    }
    const me = result.me;
    const tiles = clone(this._startDraft().tiles);
    tiles[index].screen = screen;
    tiles[index].tile = anchor;
    if (result.swap) {
      tiles[result.swap.index].screen = me.screen;
      tiles[result.swap.index].tile = me.anchor;
    }
    const across = screen !== me.screen;
    await this._commit(
      { tiles },
      result.swap
        ? `Swapped ${me.label} and ${result.swap.label}`
        : `Moved ${me.label}${across ? ` to screen ${screen}` : ""}`
    );
    if (!across) return;
    // Follow the tile to its new screen; the slides may have changed around it.
    const target = (this._slides || []).findIndex((s) => s.screen === screen && !s.isNew);
    if (target >= 0 && target !== this._screenIndex) {
      this._screenIndex = target;
      if (this._track) this._track.scrollLeft = target * this._track.clientWidth;
      this._updateDots();
    }
    // A screen holds tiles or does not exist: one left empty leaves the panel.
    if (!result.swap && !tiles.some((t) => t.screen === me.screen)) {
      this._say(`Screen ${me.screen} is now empty, so it will be taken off the panel.`);
    }
  }

  // The Move button: pick the tile up, then tap where it should go.
  _startMove(meta) {
    this._moving = { index: meta.index, screen: meta.screen, label: meta.label || meta.type_label };
    this._sheetMode = "move";
    this._renderSheet();
    this._markDropTargets();
  }

  _cancelMove() {
    this._moving = null;
    this._sheetMode = null;
    this._markDropTargets();
    this._renderSheet();
  }

  _moveTo(selection) {
    const moving = this._moving;
    const view = this._view;
    // A tile tapped is a target by its top-left corner.
    let anchor = selection.tile;
    if (selection.kind === "tile") {
      const target = this._placed(view, selection.screen).find((p) => p.anchor === selection.tile);
      if (target && target.index === moving.index) return this._cancelMove();
      anchor = target ? target.anchor : selection.tile;
    }
    this._move(moving.index, selection.screen, anchor);
  }

  // Outline where the tile being moved can go.
  _markDropTargets() {
    const moving = this._moving;
    const view = this._view;
    for (const node of this.shadowRoot.querySelectorAll(".tile, .empty")) {
      let ok = false;
      if (moving && view) {
        ok = this._dropAt(view, moving.index, Number(node.dataset.screen), Number(node.dataset.tile)).ok;
      }
      node.classList.toggle("drop-ok", ok);
      node.classList.toggle("moving", !!moving && node.classList.contains("tile")
        && Number(node.dataset.screen) === moving?.screen && this._meta(view, moving.screen, Number(node.dataset.tile))?.index === moving.index);
    }
  }

  // Drag: the mouse drags at once; a finger has to press and hold first, so a swipe
  // across the tiles still changes screens.
  _enableDrag(node, screen, tile) {
    node.addEventListener("mousedown", (e) => {
      if (e.button === 0) this._dragArm(e.clientX, e.clientY, node, screen, tile, false);
    });
    node.addEventListener(
      "touchstart",
      (e) => {
        if (e.touches.length === 1) this._dragArm(e.touches[0].clientX, e.touches[0].clientY, node, screen, tile, true);
      },
      { passive: true }
    );
    node.addEventListener("contextmenu", (e) => {
      if (this._drag) e.preventDefault();
    });
  }

  _dragArm(x, y, node, screen, tile, touch) {
    if (FORM_MODES.has(this._sheetMode) || this._sheetMode === "move" || this._drag) return;
    const meta = this._meta(this._view, screen, tile);
    if (!meta) return;
    // from: the screen it was picked up on; screen: the one it is over now.
    const drag = { node, from: screen, screen, meta, x0: x, y0: y, touch, active: false, anchor: null };
    this._drag = drag;
    const move = (e) => {
      const point = touch ? e.touches[0] : e;
      if (!point) return;
      const dx = point.clientX - drag.x0, dy = point.clientY - drag.y0;
      if (!drag.active) {
        if (touch) {
          if (Math.hypot(dx, dy) > HOLD_SLOP_PX) end(false); // a swipe, not a press
          return;
        }
        if (Math.hypot(dx, dy) < DRAG_START_PX) return;
        this._dragStart(drag);
      }
      if (touch) e.preventDefault(); // the page must not scroll while a tile is held
      this._dragTo(drag, point.clientX, point.clientY);
    };
    const end = (drop) => {
      clearTimeout(drag.timer);
      window.removeEventListener(touch ? "touchmove" : "mousemove", move);
      window.removeEventListener(touch ? "touchend" : "mouseup", up);
      window.removeEventListener("touchcancel", cancel);
      if (this._drag === drag) this._drag = null;
      if (drag.active) this._dragFinish(drag, drop);
    };
    const up = () => end(true);
    const cancel = () => end(false);
    window.addEventListener(touch ? "touchmove" : "mousemove", move, { passive: false });
    window.addEventListener(touch ? "touchend" : "mouseup", up);
    if (touch) {
      window.addEventListener("touchcancel", cancel);
      drag.timer = setTimeout(() => {
        this._dragStart(drag);
        navigator.vibrate?.(15);
      }, HOLD_MS);
    }
  }

  _dragStart(drag) {
    if (drag.active) return;
    drag.active = true;
    const canvas = drag.node.parentElement;
    const view = this._view;
    const geo = this._geometry(view);
    const { cols } = geo;
    const rect = canvas.getBoundingClientRect();
    drag.canvas = canvas;
    drag.geo = geo;
    drag.scale = rect.width / canvas.offsetWidth;
    const px = (drag.x0 - rect.left) / drag.scale, py = (drag.y0 - rect.top) / drag.scale;
    // Which cell of the tile was grabbed, so the tile moves with the pointer.
    const anchor = drag.meta.tile;
    drag.grab = {
      dx: Math.floor(px / geo.cellW) - ((anchor - 1) % cols),
      dy: Math.floor(py / geo.cellH) - Math.floor((anchor - 1) / cols),
    };
    drag.me = this._placedTile(view, drag.meta.index);
    drag.node.classList.add("dragging");
    drag.marker = el("div", { class: "drop-marker" });
    canvas.append(drag.marker);
    this._dragTo(drag, drag.x0, drag.y0);
  }

  _dragTo(drag, x, y) {
    const { canvas, geo, me } = drag;
    if (!canvas || !me) return;
    drag.x = x;
    drag.y = y;
    this._dragEdge(drag, x, y);
    const rect = canvas.getBoundingClientRect();
    const col = Math.floor((x - rect.left) / drag.scale / geo.cellW) - drag.grab.dx;
    const row = Math.floor((y - rect.top) / drag.scale / geo.cellH) - drag.grab.dy;
    const inside = col >= 0 && row >= 0 && col < geo.cols && row < geo.rows;
    drag.anchor = inside ? row * geo.cols + col + 1 : null;
    const result = inside ? this._dropAt(this._view, drag.meta.index, drag.screen, drag.anchor) : { ok: false };
    drag.ok = result.ok;
    Object.assign(drag.marker.style, {
      left: `${Math.max(0, col) * geo.cellW + geo.pad}px`,
      top: `${Math.max(0, row) * geo.cellH + geo.pad}px`,
      width: `${me.w * geo.cellW - 2 * geo.pad}px`,
      height: `${me.h * geo.cellH - 2 * geo.pad}px`,
      display: inside ? "block" : "none",
    });
    drag.marker.classList.toggle("ok", !!result.ok);
    drag.marker.classList.toggle("same", !!result.same);
  }

  // Hovering over an arrow, or past the side of the screen, changes screen after a
  // moment - and again while it stays there - with the tile still held.
  _dragEdge(drag, x, y) {
    const inRect = (node) => {
      const r = node?.getBoundingClientRect();
      return !!r && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
    };
    const [prev, next] = this.shadowRoot.querySelectorAll(".nav .arrow");
    const frame = drag.canvas.parentElement.getBoundingClientRect();
    const side = y >= frame.top && y <= frame.bottom;
    let dir = 0;
    if (inRect(prev) || (side && x < frame.left - 4)) dir = -1;
    else if (inRect(next) || (side && x > frame.right + 4)) dir = 1;
    if (dir === drag.edgeDir) return;
    clearTimeout(drag.edgeTimer);
    drag.edgeDir = dir;
    prev?.classList.toggle("hover", dir === -1);
    next?.classList.toggle("hover", dir === 1);
    if (dir) drag.edgeTimer = setTimeout(() => this._dragSwitch(drag, dir), EDGE_HOVER_MS);
  }

  _dragSwitch(drag, dir) {
    const slides = this._slides || [];
    const index = this._screenIndex + dir;
    drag.edgeDir = 0;
    if (!drag.active || index < 0 || index >= slides.length) return;
    this._screenIndex = index;
    this._track.scrollLeft = index * this._track.clientWidth;
    this._updateDots();
    const canvas = this._track.querySelectorAll(".slide")[index]?.querySelector(".screen");
    if (!canvas) return;
    canvas.append(drag.marker);
    drag.canvas = canvas;
    drag.screen = slides[index].screen;
    drag.scale = canvas.getBoundingClientRect().width / canvas.offsetWidth;
    // Still over the arrow: keep going after another pause.
    this._dragTo(drag, drag.x, drag.y);
  }

  _dragFinish(drag, drop) {
    clearTimeout(drag.edgeTimer);
    this.shadowRoot.querySelectorAll(".nav .arrow").forEach((a) => a.classList.remove("hover"));
    drag.node.classList.remove("dragging");
    drag.marker?.remove();
    this._dragEndedAt = Date.now();
    const pending = this._renderPending;
    this._renderPending = false;
    if (drop && drag.anchor) this._move(drag.meta.index, drag.screen, drag.anchor);
    else if (pending) this._renderAll();
  }

  // A tile, screen, settings or library form is being filled in.
  get _formOpen() {
    return (
      FORM_MODES.has(this._sheetMode) &&
      !!(this._form || this._screenForm || this._settingsForm || this._libraryState?.adding)
    );
  }

  // ── screens ───────────────────────────────────────────────────────────
  async _openScreen(screenNumber) {
    if (this._formOpen) {
      this._say("Finish or cancel the open form first.", true);
      return;
    }
    const view = this._view;
    const key = String(screenNumber);
    this._moving = null;
    this._selected = { kind: "screen", screen: screenNumber };
    this._sheetMode = "screen";
    this._screenForm = {
      screen: screenNumber,
      data: {
        name: view.screen_names?.[key] || "",
        color: view.screen_colors?.[key] || [0, 0, 0],
      },
      ready: null,
    };
    this._markSelected();
    this._renderSheet();
    this._screenForm.ready = await ensureHaForm();
    if (this._sheetMode === "screen") this._renderSheet();
    if (this._narrow) this._sheetEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  _renderScreenForm() {
    const sheet = this._sheetEl;
    const form = this._screenForm;
    sheet.append(
      el("h3", {}, `Screen ${form.screen}`),
      el("p", { class: "hint" }, "Its name shows at the foot of the screen on the panel.")
    );
    if (form.ready === null) {
      sheet.append(el("p", { class: "hint" }, "Loading the form…"));
      return;
    }
    if (!form.ready) {
      sheet.append(
        el("p", { class: "hint error" }, "Home Assistant's form fields didn't load on this page. Reload the page, or use the panel's Configure dialog."),
        el("div", { class: "actions" }, el("button", { onclick: () => this._closeScreen() }, "Back"))
      );
      return;
    }
    const labels = { name: "Name", color: "Background colour" };
    const helpers = {
      name: "Leave blank to show the panel's name.",
      color: "Black means no colour of its own: the screen follows the panel's background colour.",
    };
    const haForm = document.createElement("ha-form");
    haForm.computeLabel = (field) => labels[field.name];
    haForm.computeHelper = (field) => helpers[field.name];
    haForm.hass = this._hass;
    haForm.schema = [
      { name: "name", selector: { text: {} } },
      { name: "color", selector: { color_rgb: {} } },
    ];
    haForm.data = form.data;
    haForm.addEventListener("value-changed", (e) => {
      form.data = e.detail.value;
    });
    this._haForm = haForm;
    sheet.append(haForm, el(
      "div",
      { class: "actions" },
      el("button", { class: "primary", onclick: () => this._saveScreen() }, "Save"),
      el("button", { onclick: () => this._closeScreen() }, "Cancel")
    ));
  }

  _closeScreen() {
    this._screenForm = null;
    this._sheetMode = null;
    this._selected = null;
    if (this._draft && !this._draft.changes) this._clearDraft();
    this._markSelected();
    this._renderSheet();
  }

  async _saveScreen() {
    const form = this._screenForm;
    const draft = this._startDraft();
    const key = String(form.screen);
    const names = clone(draft.screen_names);
    const colours = clone(draft.screen_colors);
    const name = String(form.data.name || "").trim();
    if (name) names[key] = name;
    else delete names[key];
    const colourValue = Array.isArray(form.data.color) ? form.data.color.map(Number) : [0, 0, 0];
    // Pure black is the firmware's "no colour": the screen follows the panel's.
    if (colourValue.some((c) => c)) colours[key] = colourValue;
    else delete colours[key];
    this._screenForm = null;
    if (JSON.stringify(names) === JSON.stringify(draft.screen_names) && JSON.stringify(colours) === JSON.stringify(draft.screen_colors)) {
      return this._closeScreen(); // nothing changed
    }
    await this._commit({ screen_names: names, screen_colors: colours }, null);
    this._say(`Changed screen ${form.screen}. Not on the panel until you apply.`);
  }

  // ── panel settings ────────────────────────────────────────────────────
  // The dialog's own settings forms (display, album art), filled in from the draft,
  // one under the other with one Save.
  async _openSettings() {
    if (this._formOpen) {
      this._say("Finish or cancel the open form first.", true);
      return;
    }
    if (!this._stored) return;
    this._moving = null;
    this._selected = null;
    this._libraryState = null;
    this._markSelected();
    this._sheetMode = "settings";
    const form = { forms: null, error: null, ready: null };
    this._settingsForm = form;
    this._renderSheet();
    if (this._narrow) this._sheetEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const ready = await ensureHaForm();
    try {
      const result = await this._hass.callWS({
        type: WS.settingsForm,
        entry_id: this._stored.entry_id,
        settings: this._draft?.settings ?? null,
      });
      form.forms = result.forms.map((spec) => {
        const data = initialData(spec.schema);
        return { spec, data, start: stable(data), fieldErrors: {} };
      });
      form.ready = ready;
    } catch (err) {
      Object.assign(form, { error: err?.message || String(err), ready });
    }
    if (this._settingsForm === form) this._renderSheet();
  }

  _renderSettingsForm() {
    const sheet = this._sheetEl;
    const form = this._settingsForm;
    sheet.append(el("h3", {}, "Panel settings"));
    const back = () => el("div", { class: "actions" }, el("button", { onclick: () => this._closeSettings() }, "Back"));
    if (form.ready === null) {
      sheet.append(el("p", { class: "hint" }, "Loading the form…"));
      return;
    }
    if (!form.forms) {
      sheet.append(el("p", { class: "hint error" }, form.error || "Couldn't load the form."), back());
      return;
    }
    if (!form.ready) {
      sheet.append(
        el("p", { class: "hint error" }, "Home Assistant's form fields didn't load on this page. Reload the page, or use the panel's Configure dialog."),
        back()
      );
      return;
    }
    for (const part of form.forms) {
      const spec = part.spec;
      sheet.append(el("h4", { class: "section" }, spec.title));
      if (spec.description) sheet.append(el("p", { class: "hint" }, spec.description));
      const haForm = document.createElement("ha-form");
      haForm.dataset.form = spec.form;
      haForm.computeLabel = (field) => spec.labels?.[field.name] || field.name;
      haForm.computeHelper = (field) => spec.descriptions?.[field.name];
      haForm.hass = this._hass;
      haForm.schema = spec.schema;
      haForm.data = part.data;
      haForm.error = part.fieldErrors || {};
      haForm.addEventListener("value-changed", (e) => {
        part.data = e.detail.value;
      });
      sheet.append(haForm);
    }
    if (form.error) sheet.append(el("p", { class: "hint error" }, form.error));
    sheet.append(el(
      "div",
      { class: "actions" },
      el("button", { class: "primary", onclick: () => this._saveSettings() }, "Save"),
      el("button", { onclick: () => this._closeSettings() }, "Cancel")
    ));
  }

  _closeSettings() {
    this._settingsForm = null;
    this._sheetMode = null;
    if (this._draft && !this._draft.changes) this._clearDraft();
    this._renderSheet();
  }

  async _saveSettings() {
    const form = this._settingsForm;
    if (!form?.forms || !this._stored) return;
    // Only the forms that were changed are sent; the others stay as they are.
    const inputs = {};
    for (const part of form.forms) {
      if (stable(part.data) !== part.start) inputs[part.spec.form] = part.data;
    }
    if (!Object.keys(inputs).length) return this._closeSettings(); // nothing changed
    let result;
    try {
      result = await this._hass.callWS({
        type: WS.buildSettings,
        entry_id: this._stored.entry_id,
        settings: this._draft?.settings ?? null,
        inputs,
      });
    } catch (err) {
      form.error = err?.message || String(err);
      return this._renderSheet();
    }
    if (this._settingsForm !== form) return; // closed meanwhile
    if (result.errors) {
      form.error = "Check the values marked in red.";
      for (const part of form.forms) part.fieldErrors = result.errors[part.spec.form] || {};
      return this._renderSheet();
    }
    this._settingsForm = null;
    await this._commit({ settings: result.settings }, null);
    this._say("Changed the panel settings. Not on the panel until you apply.");
  }

  // ── the image and icon library ────────────────────────────────────────
  // Shared by every panel: adding and deleting take effect at once, not on Apply.
  async _openLibrary() {
    if (this._formOpen) {
      this._say("Finish or cancel the open form first.", true);
      return;
    }
    this._moving = null;
    this._selected = null;
    this._markSelected();
    this._settingsForm = null;
    this._sheetMode = "library";
    this._libraryState = { data: null, error: null, picked: null, confirm: false, adding: null, busy: false };
    this._renderSheet();
    if (this._narrow) this._sheetEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    await this._loadLibrary();
  }

  async _loadLibrary() {
    const state = this._libraryState;
    if (!state) return;
    try {
      state.data = await this._hass.callWS({ type: WS.library });
      state.error = null;
    } catch (err) {
      state.error = err?.message || String(err);
    }
    if (this._libraryState === state) this._renderSheet();
  }

  _closeLibrary() {
    this._libraryState = null;
    this._sheetMode = null;
    this._renderSheet();
  }

  _renderLibrary() {
    const sheet = this._sheetEl;
    const state = this._libraryState;
    sheet.append(
      el("h3", {}, "Images and icons"),
      el("p", { class: "hint" }, "The library every panel shares. Adding or deleting here happens straight away - it is not part of Apply to panel.")
    );
    if (state.error) {
      sheet.append(el("p", { class: "hint error" }, state.error));
    }
    if (!state.data) {
      if (!state.error) sheet.append(el("p", { class: "hint" }, "Loading the library…"));
      sheet.append(el("div", { class: "actions" }, el("button", { onclick: () => this._closeLibrary() }, "Close")));
      return;
    }
    if (state.adding) return this._renderLibraryAdd();
    const data = state.data;
    const card = (kind, item) => {
      const picked = state.picked && state.picked.kind === kind && state.picked.id === item.id;
      const picture = !item.uri
        ? null
        : kind === "image"
          ? el("img", { class: "lib-image", src: item.uri, alt: "" })
          : el("span", { class: "lib-icon", style: { maskImage: `url("${item.uri}")`, webkitMaskImage: `url("${item.uri}")` } });
      return el(
        "button",
        {
          class: `lib-card${picked ? " on" : ""}`,
          title: item.name,
          onclick: () => {
            state.picked = picked ? null : { kind, id: item.id };
            state.confirm = false;
            this._renderSheet();
          },
        },
        el("span", { class: "lib-picture" }, picture),
        el("span", { class: "lib-name" }, item.name),
        item.used_by.length ? el("span", { class: "lib-used" }, `${item.used_by.length} tile${item.used_by.length === 1 ? "" : "s"}`) : null
      );
    };
    const details = (kind, list) => {
      const item = state.picked?.kind === kind && list.find((i) => i.id === state.picked.id);
      return item ? this._libraryDetails(kind, item) : null;
    };

    sheet.append(el(
      "div",
      { class: "lib-head" },
      el("h4", { class: "section" }, `Background images (${data.images.length})`),
      el("button", { class: "settings-button", onclick: () => this._startLibraryAdd("image") }, "Add image")
    ));
    if (!data.images.length) sheet.append(el("p", { class: "hint" }, "No background images yet."));
    else sheet.append(el("div", { class: "lib-grid" }, data.images.map((i) => card("image", i))));
    add(sheet, details("image", data.images));

    sheet.append(el(
      "div",
      { class: "lib-head" },
      el("h4", { class: "section" }, `Custom icons (${data.icons.length})`),
      el("button", { class: "settings-button", onclick: () => this._startLibraryAdd("icon") }, "Add icon")
    ));
    if (!data.icons.length) sheet.append(el("p", { class: "hint" }, "No custom icons yet."));
    let category = null;
    let grid = null;
    for (const icon of data.icons) {
      if (icon.category_label !== category) {
        category = icon.category_label;
        grid = el("div", { class: "lib-grid" });
        sheet.append(el("div", { class: "field-label" }, category), grid);
      }
      grid.append(card("icon", icon));
    }
    add(sheet, details("icon", data.icons));
    sheet.append(el("div", { class: "actions" }, el("button", { onclick: () => this._closeLibrary() }, "Close")));
  }

  _libraryDetails(kind, item) {
    const state = this._libraryState;
    const what = kind === "image" ? "image" : "icon";
    const rows = [["Name", item.name]];
    const size = item.size < 1024 ? `${item.size} bytes` : `${(item.size / 1024).toFixed(1)} KB`;
    if (kind === "image") rows.push(["Format", `${item.format.toUpperCase()}, ${size}`]);
    else rows.push(["Category", item.category_label + (item.bundled ? " (comes with the integration)" : "")]);
    const used = item.used_by.length
      ? el("ul", { class: "lib-uses" }, item.used_by.slice(0, 6).map((u) => el("li", {}, u)),
          item.used_by.length > 6 ? el("li", {}, `and ${item.used_by.length - 6} more`) : null)
      : el("p", { class: "hint" }, "No tile uses it.");
    const box = el(
      "div",
      { class: "lib-details" },
      el("dl", {}, rows.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
      el("div", { class: "field-label" }, "Used by"),
      used
    );
    if (!state.confirm) {
      box.append(el("div", { class: "actions" }, el("button", { class: "danger", onclick: () => { state.confirm = true; this._renderSheet(); } }, `Delete ${what}`)));
      return box;
    }
    const warning = [
      `Delete ${item.name} from the library? It goes from every panel, and can't be undone here.`,
      item.used_by.length
        ? ` ${item.used_by.length} tile${item.used_by.length === 1 ? "" : "s"} use${item.used_by.length === 1 ? "s" : ""} it and will lose ${kind === "image" ? "the background image" : "the icon"} the next time ${item.used_by.length === 1 ? "its" : "their"} panel restarts or is sent its settings.`
        : "",
      item.bundled ? " It comes with the integration, and stays deleted after restarts." : "",
    ].join("");
    box.append(
      el("p", { class: "hint error" }, warning),
      el(
        "div",
        { class: "actions" },
        el("button", { class: "danger", disabled: state.busy, onclick: () => this._libraryDelete(kind, item) }, "Delete"),
        el("button", { onclick: () => { state.confirm = false; this._renderSheet(); } }, "Cancel")
      )
    );
    return box;
  }

  async _libraryDelete(kind, item) {
    const state = this._libraryState;
    state.busy = true;
    this._renderSheet();
    try {
      await this._hass.callWS({ type: WS.libraryDelete, kind, item_id: item.id });
      this._say(`Deleted ${item.name} from the library.`);
      state.picked = null;
      state.confirm = false;
    } catch (err) {
      this._say(err?.message || String(err), true);
    }
    state.busy = false;
    await this._libraryChanged();
  }

  // The library changed: re-read it, and redraw the panels, whose pictures come from it.
  async _libraryChanged() {
    await this._loadLibrary();
    await this._fetch();
  }

  _startLibraryAdd(kind) {
    const state = this._libraryState;
    state.adding = { kind, name: "", category: "misc", data: "", fileName: "", error: null };
    state.picked = null;
    state.confirm = false;
    this._renderSheet();
  }

  _renderLibraryAdd() {
    const sheet = this._sheetEl;
    const state = this._libraryState;
    const adding = state.adding;
    const icon = adding.kind === "icon";
    sheet.append(el("h4", { class: "section" }, icon ? "Add a custom icon" : "Add a background image"));
    sheet.append(el(
      "p",
      { class: "hint" },
      icon
        ? "A PNG, 60 × 60 like the built-in icons: it is drawn in white, or in the icon-on colour when the tile is on. It becomes available on every panel."
        : "A PNG, JPG or GIF sized for the tile it goes on (a 1 × 1 tile is about 140 px). It becomes available on every panel."
    ));
    const nameInput = el("input", { class: "text", type: "text", value: adding.name, placeholder: "Name", "aria-label": "Name" });
    nameInput.addEventListener("input", () => { adding.name = nameInput.value; });
    sheet.append(el("div", { class: "field-label" }, "Name"), nameInput);
    if (icon) {
      const select = el(
        "select",
        { "aria-label": "Category" },
        state.data.categories.map((c) => el("option", { value: c.value, selected: c.value === adding.category }, c.label))
      );
      select.addEventListener("change", () => { adding.category = select.value; });
      sheet.append(el("div", { class: "field-label" }, "Category"), select);
    }
    const file = el("input", { type: "file", accept: icon ? "image/png" : "image/png,image/jpeg,image/gif", "aria-label": "Picture file" });
    file.addEventListener("change", () => {
      const chosen = file.files?.[0];
      if (!chosen) return;
      const reader = new FileReader();
      reader.onload = () => {
        adding.data = String(reader.result || "");
        adding.fileName = chosen.name;
        if (!adding.name) adding.name = chosen.name.replace(/\.[^.]+$/, "");
        adding.error = null;
        this._renderSheet();
      };
      reader.readAsDataURL(chosen);
    });
    sheet.append(el("div", { class: "field-label" }, "Picture"), file);
    const paste = el("textarea", { class: "text", rows: 3, placeholder: "…or paste the imageBase64 value from the OXRS Asset Generator", "aria-label": "Base64" });
    paste.value = adding.fileName ? "" : adding.data;
    paste.addEventListener("input", () => { adding.data = paste.value.trim(); adding.fileName = ""; this._renderLibraryPreview(); });
    sheet.append(paste);
    sheet.append(el("div", { class: "lib-preview" }));
    if (adding.error) sheet.append(el("p", { class: "hint error" }, adding.error));
    sheet.append(el(
      "div",
      { class: "actions" },
      el("button", { class: "primary", disabled: state.busy, onclick: () => this._libraryAdd() }, "Add to library"),
      el("button", { onclick: () => { state.adding = null; this._renderSheet(); } }, "Cancel")
    ));
    this._renderLibraryPreview();
  }

  // What was chosen, drawn as it will look, with its encoded size against the limits.
  _renderLibraryPreview() {
    const state = this._libraryState;
    const adding = state?.adding;
    const box = this._sheetEl.querySelector(".lib-preview");
    if (!adding || !box) return;
    box.replaceChildren();
    const raw = adding.data.replace(/^data:[^,]*,/, "");
    if (!raw) return;
    const uri = adding.data.startsWith("data:") ? adding.data : `data:image/png;base64,${raw}`;
    const picture =
      adding.kind === "image"
        ? el("img", { class: "lib-image", src: uri, alt: "" })
        : el("span", { class: "lib-icon", style: { maskImage: `url("${uri}")`, webkitMaskImage: `url("${uri}")` } });
    const size = raw.length;
    const note =
      size > state.data.max_size
        ? el("span", { class: "error" }, `${(size / 1024).toFixed(1)} KB encoded - too large (the limit is ${state.data.max_size / 1024} KB).`)
        : size > state.data.safe_size
          ? el("span", { class: "warn" }, `${(size / 1024).toFixed(1)} KB encoded - over the ${state.data.safe_size / 1024} KB the OXRS docs call safe: the panel may not draw it, or may restart.`)
          : el("span", {}, `${(size / 1024).toFixed(1)} KB encoded${adding.fileName ? ` · ${adding.fileName}` : ""}`);
    box.append(el("span", { class: "lib-picture" }, picture), note);
  }

  async _libraryAdd() {
    const state = this._libraryState;
    const adding = state?.adding;
    if (!adding || state.busy) return;
    state.busy = true;
    let result;
    try {
      result = await this._hass.callWS({
        type: WS.libraryAdd,
        kind: adding.kind,
        name: adding.name,
        data: adding.data,
        ...(adding.kind === "icon" ? { category: adding.category } : {}),
      });
    } catch (err) {
      result = { error: err?.message || String(err) };
    }
    state.busy = false;
    if (this._libraryState !== state) return;
    if (result.error) {
      adding.error = result.error;
      return this._renderSheet();
    }
    const name = adding.name.trim();
    state.adding = null;
    this._say(
      result.replaced
        ? `Replaced the icon ${name} in the library.`
        : `Added ${name} to the library. Choose it for a tile when you add or edit one.`
    );
    await this._libraryChanged();
  }

  // ── picking and the side sheet ────────────────────────────────────────
  _pick(selection) {
    if (Date.now() - (this._dragEndedAt || 0) < 400) return; // the click that ends a drag
    if (this._sheetMode === "move" && this._moving) return this._moveTo(selection);
    if (this._formOpen) {
      this._say("Finish or cancel the open form first.", true);
      return;
    }
    this._selected = selection;
    this._sheetMode = selection.kind === "empty" ? "types" : null;
    this._markSelected();
    this._renderSheet();
    if (this._narrow) this._sheetEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  _markSelected() {
    for (const node of this.shadowRoot.querySelectorAll(".tile, .empty")) {
      const s = this._selected;
      const kind = node.classList.contains("tile") ? "tile" : "empty";
      node.classList.toggle(
        "selected",
        !!s && s.kind === kind && String(s.screen) === node.dataset.screen && String(s.tile) === node.dataset.tile
      );
    }
  }

  _meta(view, screen, tile) {
    return (view?.tiles || []).find((t) => t.screen === screen && t.tile === tile);
  }

  _renderSheet() {
    if (!this._built) return;
    const sheet = this._sheetEl;
    sheet.replaceChildren();
    this._haForm = null;
    const view = this._view;
    const s = this._selected;
    // Move mode stands on its own: it must show whatever happened to the selection.
    if (this._sheetMode === "move" && this._moving) {
      add(
        sheet,
        el("h3", {}, `Move ${this._moving.label}`),
        el("p", { class: "hint" }, "Tap where it should go: an outlined space, or a tile of the same size to swap with. To move it to another screen, swipe or use the arrows first; the last screen is a new one."),
        el("div", { class: "actions" }, el("button", { onclick: () => this._cancelMove() }, "Cancel"))
      );
      return;
    }
    if (this._sheetMode === "settings" && this._settingsForm) return this._renderSettingsForm();
    if (this._sheetMode === "library" && this._libraryState) return this._renderLibrary();
    if (!view || !s) {
      sheet.append(el("p", { class: "hint" }, "Tap a tile to change, move or remove it, or an empty space to add a tile there. Drag a tile to move it (on a phone: press and hold it first); hold it over an arrow to take it to another screen. Tap a screen's name to rename or recolour it. Swipe, or use the arrows, to change screens; the last screen is a new one."));
      return;
    }
    if (this._sheetMode === "form" || this._sheetMode === "playlists") return this._renderForm();
    if (this._sheetMode === "screen" && this._screenForm) return this._renderScreenForm();
    if (s.kind === "empty") return this._renderTypes(s);
    if (s.kind === "screen") return;
    this._renderTileInfo(view, s);
  }

  _renderTileInfo(view, s) {
    const sheet = this._sheetEl;
    const meta = this._meta(view, s.screen, s.tile);
    if (!meta) {
      sheet.append(el("p", { class: "hint" }, "This tile is no longer here."));
      return;
    }
    const state = this._state(view, s.screen, s.tile);
    const entity = meta.entity_id ? this._hass?.states[meta.entity_id] : null;
    const rows = [
      ["Type", meta.type_label],
      ["Entity", entity ? `${entity.attributes.friendly_name || meta.entity_id} (${meta.entity_id})` : meta.entity_id || "none"],
      ["State", entity ? entity.state : state.state || "-"],
      ["Position", `${meta.tile} on screen ${meta.screen}`],
      ["Size", `${meta.size[0]} × ${meta.size[1]}`],
    ];
    if (meta.album_art) rows.push(["Album art", "shown"]);
    add(sheet,
      el("h3", {}, meta.label || meta.type_label),
      el("dl", {}, rows.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
      el(
        "div",
        { class: "actions" },
        meta.type
          ? el("button", { class: "primary", onclick: () => this._openForm({ index: meta.index }) }, "Edit")
          : null,
        el("button", { onclick: () => this._startMove(meta) }, "Move"),
        el("button", { class: "danger", onclick: () => this._remove(meta) }, "Remove")
      ),
      meta.type ? null : el("p", { class: "hint" }, "This is an older action tile. It can be removed here, but not edited.")
    );
  }

  _renderTypes(s) {
    const sheet = this._sheetEl;
    sheet.append(
      el("h3", {}, "Add a tile"),
      el("p", { class: "hint" }, `Screen ${s.screen}, position ${s.tile}. Choose what kind of tile:`),
      el(
        "div",
        { class: "types" },
        this._types.map((t) =>
          el(
            "button",
            { class: "type", onclick: () => this._openForm({ tileType: t.type, screen: s.screen, position: s.tile }) },
            BUILTIN_ICONS.has(t.icon)
              ? el("span", { class: "type-icon", style: { maskImage: `url("${this._static}/icons/${t.icon}.png")`, webkitMaskImage: `url("${this._static}/icons/${t.icon}.png")` } })
              : el("span", { class: "type-icon blank" }),
            el("span", {}, t.label)
          )
        )
      )
    );
  }

  async _remove(meta) {
    const draft = this._startDraft();
    const tiles = draft.tiles.filter((_, i) => i !== meta.index);
    await this._commit({ tiles }, `Removed ${meta.label || meta.type_label}`);
  }

  // ── the tile form ─────────────────────────────────────────────────────
  async _openForm(target) {
    const draft = this._startDraft();
    this._sheetMode = "form";
    this._form = { target, spec: null, data: {}, size: "1x1", error: null, busy: true };
    this._renderSheet();
    const ready = await ensureHaForm();
    try {
      const spec = await this._hass.callWS({
        type: WS.tileForm,
        entry_id: draft.entry_id,
        tiles: draft.tiles,
        ...(target.index !== undefined
          ? { index: target.index }
          : { tile_type: target.tileType, screen: target.screen, position: target.position }),
      });
      this._form = { target, spec, data: initialData(spec.schema), size: spec.size, error: null, busy: false, ready };
    } catch (err) {
      this._form = { target, spec: null, error: err?.message || String(err), busy: false, ready };
    }
    this._renderSheet();
  }

  _closeForm() {
    this._sheetMode = this._selected?.kind === "empty" ? "types" : null;
    this._form = null;
    if (this._draft && !this._draft.changes) this._clearDraft();
    this._renderSheet();
  }

  _renderForm() {
    const sheet = this._sheetEl;
    const form = this._form;
    const editing = form?.target?.index !== undefined;
    if (!form || form.busy) {
      sheet.append(el("p", { class: "hint" }, "Loading the form…"));
      return;
    }
    if (!form.spec) {
      sheet.append(
        el("p", { class: "hint error" }, form.error || "Couldn't load the form."),
        el("div", { class: "actions" }, el("button", { onclick: () => this._closeForm() }, "Back"))
      );
      return;
    }
    const spec = form.spec;
    sheet.append(el("h3", {}, `${editing ? "Edit" : "Add"} · ${spec.type_label}`));
    sheet.append(el("p", { class: "hint" }, `Screen ${spec.screen}, position ${spec.position}`));

    if (!form.ready) {
      sheet.append(
        el("p", { class: "hint error" }, "Home Assistant's form fields didn't load on this page. Reload the page, or use the panel's Configure dialog."),
        el("div", { class: "actions" }, el("button", { onclick: () => this._closeForm() }, "Back"))
      );
      return;
    }

    if (this._sheetMode === "playlists") return this._renderPlaylists();

    // Size: every size that fits here, as chips.
    sheet.append(el("div", { class: "field-label" }, "Size"));
    sheet.append(el(
      "div",
      { class: "chips" },
      spec.sizes.map((s) =>
        el("button", {
          class: s.value === form.size ? "chip on" : "chip",
          onclick: () => {
            form.size = s.value;
            sheet.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.dataset.value === s.value));
          },
          "data-value": s.value,
        }, s.label)
      )
    ));

    const haForm = document.createElement("ha-form");
    haForm.computeLabel = (field) => spec.labels?.[field.name] || field.name;
    haForm.computeHelper = (field) => spec.descriptions?.[field.name];
    haForm.hass = this._hass;
    haForm.schema = spec.schema;
    haForm.data = form.data;
    haForm.error = form.fieldErrors || {};
    haForm.addEventListener("value-changed", (e) => {
      form.data = e.detail.value;
    });
    this._haForm = haForm;
    sheet.append(haForm);

    if (form.error) sheet.append(el("p", { class: "hint error" }, form.error));
    sheet.append(el(
      "div",
      { class: "actions" },
      el("button", { class: "primary", onclick: () => this._submitForm() }, editing ? "Save changes" : "Add to screen"),
      el("button", { onclick: () => this._closeForm() }, "Cancel")
    ));
  }

  _renderPlaylists() {
    const sheet = this._sheetEl;
    const form = this._form;
    const choice = form.playlistChoice;
    sheet.append(el("p", { class: "hint" }, `Tick up to ${choice.max} playlists. They appear in the tile's list on the panel.`));
    const haForm = document.createElement("ha-form");
    haForm.computeLabel = () => form.spec.labels?.playlists || "Playlists";
    haForm.hass = this._hass;
    haForm.schema = [{ name: "playlists", required: true, selector: { select: { multiple: true, mode: "list", options: choice.options } } }];
    haForm.data = { playlists: form.playlists || choice.preselect || [] };
    haForm.error = form.fieldErrors || {};
    haForm.addEventListener("value-changed", (e) => {
      form.playlists = e.detail.value.playlists || [];
    });
    this._haForm = haForm;
    sheet.append(haForm);
    if (form.error) sheet.append(el("p", { class: "hint error" }, form.error));
    sheet.append(el(
      "div",
      { class: "actions" },
      el("button", { class: "primary", onclick: () => this._submitForm() }, "Done"),
      el("button", { onclick: () => { this._sheetMode = "form"; this._renderSheet(); } }, "Back")
    ));
  }

  async _submitForm() {
    const form = this._form;
    const draft = this._draft;
    if (!form || !form.spec || !draft) return;
    const target = form.target;
    const message = {
      type: WS.buildTile,
      entry_id: draft.entry_id,
      tiles: draft.tiles,
      size: form.size,
      input: form.data,
      ...(target.index !== undefined
        ? { index: target.index }
        : { tile_type: target.tileType, screen: target.screen, position: target.position }),
    };
    if (this._sheetMode === "playlists") message.playlists = form.playlists || form.playlistChoice.preselect || [];
    let result;
    try {
      result = await this._hass.callWS(message);
    } catch (err) {
      form.error = err?.message || String(err);
      return this._renderSheet();
    }
    if (result.errors) {
      form.error = result.errors.base || result.errors.size || null;
      form.fieldErrors = Object.fromEntries(Object.entries(result.errors).filter(([k]) => k !== "base" && k !== "size"));
      if (result.errors.playlists) form.error = result.errors.playlists;
      return this._renderSheet();
    }
    if (result.playlists) {
      form.playlistChoice = { options: result.playlists, preselect: result.preselect || [], max: result.max };
      form.error = null;
      form.fieldErrors = {};
      this._sheetMode = "playlists";
      return this._renderSheet();
    }
    const tiles = clone(draft.tiles);
    if (target.index !== undefined) tiles[target.index] = result.tile;
    else tiles.push(result.tile);
    const name = result.tile.label || form.spec.type_label;
    await this._commit({ tiles }, null);
    this._say(`${target.index !== undefined ? "Changed" : "Added"} ${name}. Not on the panel until you apply.`);
  }

  // ── the bar: pending changes, undo, messages ──────────────────────────
  _renderBar() {
    if (!this._built) return;
    const bar = this._barEl;
    bar.replaceChildren();
    const draft = this._draft;
    const pending = draft && draft.changes > 0;
    const stale = pending && this._stored && this._stored.fingerprint !== draft.base;
    if (!pending && !this._undo && !this._message) {
      bar.classList.remove("show");
      return;
    }
    bar.classList.add("show");
    if (this._message) bar.append(el("span", { class: this._message.error ? "msg error" : "msg" }, this._message.text));
    if (this._undo) {
      bar.append(el("span", { class: "msg" }, this._undo.label), el("button", { onclick: () => this._doUndo() }, "Undo"));
    }
    if (pending) {
      if (stale) bar.append(el("span", { class: "msg error" }, "The tiles were changed elsewhere meanwhile."));
      bar.append(
        el("span", { class: "msg pending" }, `${draft.changes} change${draft.changes === 1 ? "" : "s"} not on the panel yet`),
        el("span", { class: "spacer" }),
        el("button", { onclick: () => this._discard(), disabled: this._applying }, "Discard"),
        el("button", { class: "primary", onclick: () => this._apply(), disabled: this._applying }, this._applying ? "Applying…" : "Apply to panel")
      );
    } else {
      bar.append(el("span", { class: "spacer" }), el("button", { "aria-label": "Close", onclick: () => this._say(null) }, "×"));
    }
  }
}

const STYLE = `
:host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
.toolbar { display: flex; align-items: center; gap: 8px; height: 56px; padding: 0 12px; border-bottom: 1px solid var(--divider-color); background: var(--app-header-background-color, var(--primary-background-color)); color: var(--app-header-text-color, var(--primary-text-color)); }
.title { font-size: 20px; flex: 1; }
:host(:not([narrow])) ha-menu-button { display: none; }
select { font: inherit; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
.message { padding: 24px; color: var(--secondary-text-color); }
.message.error, .error { color: var(--error-color); }
.main { display: flex; gap: 16px; padding: 16px; align-items: flex-start; }
:host([narrow]) .main { flex-direction: column; align-items: stretch; padding: 8px; }
.stage { flex: 1; min-width: 0; max-width: 600px; }
:host([narrow]) .stage { max-width: none; width: 100%; }
.info { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; color: var(--secondary-text-color); font-size: 14px; margin-bottom: 8px; }
.info .name { color: var(--primary-text-color); font-size: 16px; font-weight: 500; }
.offline { color: var(--error-color); }
.info-actions { margin-left: auto; display: flex; gap: 6px; }
.settings-button { font: inherit; font-size: 14px; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; }
.settings-button:hover { border-color: var(--primary-color); }
.track { display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scrollbar-width: none; }
.track::-webkit-scrollbar { display: none; }
.slide { flex: 0 0 100%; scroll-snap-align: start; display: flex; flex-direction: column; align-items: center; padding: 0 8px; box-sizing: border-box; }
.screen-name { font-size: 15px; margin-bottom: 8px; align-self: center; }
.screen-name span { color: var(--secondary-text-color); }
.screen-name.editable { font: inherit; font-size: 15px; background: transparent; border: 0; color: var(--primary-text-color); cursor: pointer; padding: 2px 8px; border-radius: 6px; }
.screen-name.editable:hover { background: var(--divider-color); }
.tile { touch-action: pan-x pan-y; -webkit-touch-callout: none; user-select: none; -webkit-user-select: none; }
.tile.dragging, .tile.moving { opacity: 0.45; }
.tile.drop-ok, .empty.drop-ok { outline: 3px dashed var(--success-color, #4caf50); outline-offset: 2px; }
.drop-marker { position: absolute; display: none; border-radius: 5px; border: 3px dashed var(--error-color); background: rgba(219, 68, 55, 0.15); pointer-events: none; box-sizing: border-box; }
.drop-marker.ok { border-color: var(--success-color, #4caf50); background: rgba(76, 175, 80, 0.2); }
.drop-marker.same { border-color: rgba(255,255,255,0.4); background: transparent; }
.frame { position: relative; overflow: hidden; border-radius: 12px; box-shadow: 0 0 0 1px var(--divider-color); }
.screen { position: absolute; top: 0; left: 0; transform-origin: 0 0; font-family: Montserrat, Roboto, sans-serif; }
.tile, .empty { position: absolute; padding: 0; margin: 0; border: 0; border-radius: 5px; overflow: hidden; cursor: pointer; background: transparent; font: inherit; text-align: left; }
.tile.selected, .empty.selected { outline: 4px solid var(--primary-color); outline-offset: 2px; }
.empty { border: 2px dashed rgba(255,255,255,0.18); }
.empty .pos { position: absolute; top: 6px; left: 8px; color: rgba(255,255,255,0.35); font-size: 14px; }
.empty .plus { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: rgba(255,255,255,0.3); font-size: 40px; }
.empty:hover { border-color: rgba(255,255,255,0.45); }
.empty:hover .plus { color: rgba(255,255,255,0.6); }
.layer { position: absolute; inset: 0; }
.clip { overflow: hidden; }
.bgimg { position: absolute; top: 50%; left: 50%; image-rendering: pixelated; }
.art { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.icon { position: absolute; top: 0; left: 0; width: 60px; height: 60px; mask-size: 60px 60px; -webkit-mask-size: 60px 60px; mask-repeat: no-repeat; -webkit-mask-repeat: no-repeat; }
.control { position: absolute; right: 0; width: 50%; height: 50%; mask-position: center; -webkit-mask-position: center; mask-repeat: no-repeat; -webkit-mask-repeat: no-repeat; }
.icontext { position: absolute; top: 4px; left: 8px; right: 8px; font-size: 20px; white-space: nowrap; overflow: hidden; }
.number { position: absolute; top: 8px; left: 8px; right: 8px; }
.number .value { font-size: 50px; line-height: 1; }
.number .units { font-size: 20px; margin-left: 5px; }
.number .subvalue { font-size: 20px; margin-top: 5px; opacity: 0.9; }
.label, .sublabel { position: absolute; left: 8px; right: 8px; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.label { bottom: 22px; }
.sublabel { bottom: 5px; opacity: 0.7; }
.footer { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: space-between; padding: 0 10px; color: #ccc; font-size: 14px; box-sizing: border-box; }
.footer-icon { font-size: 18px; opacity: 0.7; }
.arrow.hover { border-color: var(--primary-color); background: var(--primary-color); color: var(--text-primary-color, #fff); }
.nav { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 12px; }
.arrow { font-size: 26px; line-height: 1; width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); cursor: pointer; }
.dots { display: flex; gap: 6px; align-items: center; }
.dot { width: 10px; height: 10px; border-radius: 50%; border: 0; padding: 0; background: var(--divider-color); cursor: pointer; }
.dot.new { background: transparent; box-shadow: inset 0 0 0 1.5px var(--divider-color); }
.dot.on { background: var(--primary-color); box-shadow: none; }
.sheet { width: 340px; flex: 0 0 auto; background: var(--card-background-color); border-radius: 12px; padding: 16px; box-sizing: border-box; box-shadow: var(--ha-card-box-shadow, 0 0 0 1px var(--divider-color)); }
:host([narrow]) .sheet { width: 100%; }
.sheet h3 { margin: 0 0 4px; font-size: 18px; font-weight: 500; }
.sheet dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin: 12px 0; font-size: 14px; }
.sheet dt { color: var(--secondary-text-color); }
.sheet dd { margin: 0; word-break: break-word; }
.hint { color: var(--secondary-text-color); font-size: 14px; margin: 0 0 12px; }
.hint.error { color: var(--error-color); }
button { font: inherit; }
.actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.actions button, .bar button { padding: 8px 14px; border-radius: 8px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; }
.actions button.primary, .bar button.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.actions button.danger { color: var(--error-color); border-color: var(--error-color); }
button[disabled] { opacity: 0.5; cursor: default; }
.types { display: grid; grid-template-columns: 1fr; gap: 6px; }
.type { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; text-align: left; }
.type:hover { border-color: var(--primary-color); }
.type-icon { width: 28px; height: 28px; flex: 0 0 28px; background: var(--primary-text-color); mask-size: 28px 28px; -webkit-mask-size: 28px 28px; mask-repeat: no-repeat; -webkit-mask-repeat: no-repeat; }
.type-icon.blank { background: transparent; }
.field-label { font-size: 14px; color: var(--secondary-text-color); margin: 8px 0 6px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.chip { padding: 6px 10px; border-radius: 16px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; font-size: 13px; }
.chip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.bar { position: sticky; bottom: 0; display: none; align-items: center; flex-wrap: wrap; gap: 10px; padding: 10px 16px; background: var(--card-background-color); border-top: 1px solid var(--divider-color); z-index: 2; }
.bar.show { display: flex; }
.msg { font-size: 14px; }
.msg.pending { font-weight: 500; }
.spacer { flex: 1; }
.section { margin: 16px 0 6px; font-size: 15px; font-weight: 500; }
.lib-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.lib-head .section { margin-bottom: 6px; }
.lib-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(72px, 1fr)); gap: 6px; margin-bottom: 8px; }
.lib-card { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 6px 4px; border-radius: 8px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; min-width: 0; }
.lib-card.on { border-color: var(--primary-color); box-shadow: 0 0 0 1px var(--primary-color); }
.lib-picture { width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; background: #1b1b1b; border-radius: 6px; overflow: hidden; flex: 0 0 auto; }
.lib-image { max-width: 48px; max-height: 48px; image-rendering: pixelated; }
.lib-icon { width: 40px; height: 40px; background: #fff; mask-size: contain; -webkit-mask-size: contain; mask-repeat: no-repeat; -webkit-mask-repeat: no-repeat; mask-position: center; -webkit-mask-position: center; }
.lib-name { font-size: 12px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lib-used { font-size: 11px; color: var(--secondary-text-color); }
.lib-details { border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; margin: 4px 0 8px; }
.lib-details dl { margin: 4px 0; }
.lib-uses { margin: 0 0 4px; padding-left: 18px; font-size: 13px; }
.lib-preview { display: flex; align-items: center; gap: 10px; margin: 8px 0; font-size: 13px; color: var(--secondary-text-color); }
.lib-preview .warn { color: var(--warning-color, #ffa600); }
.lib-preview .error { color: var(--error-color); }
input.text, textarea.text { width: 100%; box-sizing: border-box; font: inherit; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
textarea.text { margin-top: 8px; font-family: monospace; font-size: 12px; resize: vertical; }
input[type=file] { font: inherit; font-size: 13px; color: var(--primary-text-color); max-width: 100%; }
`;

customElements.define("oxrs-panel-editor", OxrsPanelEditor);
