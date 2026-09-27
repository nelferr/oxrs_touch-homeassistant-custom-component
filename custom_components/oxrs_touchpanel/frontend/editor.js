// OXRS panels - a sidebar page that draws each panel's screens as the panel shows them,
// and changes their tiles.
//
// Everything drawn comes from the integration's websocket commands, which return the
// same conf payload and tile states it publishes to the panel - for a draft of unsaved
// changes too - so this page cannot drift from the panel. Tile forms are built by the
// options dialog's own code and rendered with Home Assistant's own <ha-form>.
//
// Edits are staged: they change a draft that is drawn as a preview, and nothing
// reaches the panel until "Apply to panel", which sends the whole draft once.
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
      this._draft = { entry_id: stored.entry_id, base: stored.fingerprint, tiles: clone(stored.config_tiles), changes: 0 };
    }
    return this._draft;
  }

  _clearDraft() {
    this._draft = null;
    this._preview = null;
    this._dropUndo();
  }

  async _commit(tiles, label) {
    const draft = this._startDraft();
    const before = { tiles: clone(draft.tiles), changes: draft.changes };
    draft.tiles = tiles;
    draft.changes += 1;
    this._message = null; // a new change replaces the last one's message
    this._selected = null;
    this._sheetMode = null;
    this._form = null;
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
    this._renderAll();
  }

  _renderAll() {
    if (!this._built) return;
    this._renderToolbar();
    this._renderStage();
    // A form being filled in is left alone; everything else follows the data.
    if (this._sheetMode !== "form" && this._sheetMode !== "playlists") this._renderSheet();
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
      view.available ? null : el("span", { class: "offline" }, "offline")
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
    if (this._sheetMode === "form" || this._sheetMode === "playlists") return; // keep an open form
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
      el(
        "div",
        { class: "screen-name" },
        screen.isNew ? "New screen" : screen.label || `Screen ${screen.screen}`,
        el("span", {}, screen.isNew ? ` · add a tile to create screen ${screen.screen}` : ` · screen ${screen.screen}`)
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
    return node;
  }

  // ── picking and the side sheet ────────────────────────────────────────
  _pick(selection) {
    if ((this._sheetMode === "form" || this._sheetMode === "playlists") && this._form) {
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
    if (!view || !s) {
      sheet.append(el("p", { class: "hint" }, "Tap a tile to change or remove it, or an empty space to add a tile there. Swipe, or use the arrows, to change screens; the last screen is a new one."));
      return;
    }
    if (this._sheetMode === "form" || this._sheetMode === "playlists") return this._renderForm();
    if (s.kind === "empty") return this._renderTypes(s);
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
    await this._commit(tiles, `Removed ${meta.label || meta.type_label}`);
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
    await this._commit(tiles, null);
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
.track { display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scrollbar-width: none; }
.track::-webkit-scrollbar { display: none; }
.slide { flex: 0 0 100%; scroll-snap-align: start; display: flex; flex-direction: column; align-items: center; padding: 0 8px; box-sizing: border-box; }
.screen-name { font-size: 15px; margin-bottom: 8px; align-self: center; }
.screen-name span { color: var(--secondary-text-color); }
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
`;

customElements.define("oxrs-panel-editor", OxrsPanelEditor);
