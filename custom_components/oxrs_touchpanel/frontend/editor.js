// OXRS panels - a sidebar page that draws each panel's screens as the panel shows them.
//
// Read-only for now. Everything drawn comes from the websocket command
// oxrs_touchpanel/editor/panels, which returns the same conf payload and tile states
// the integration publishes to the panel, so this page cannot drift from it.
//
// Geometry and look follow the firmware (OXRS-IO-TouchPanel-ESP32-FW):
//   cell = (screen width / cols) x ((screen height - 33 footer) / rows), integer division;
//   a tile is cells x span less 5 px padding on each side;
//   the icon (60 x 60) sits at the tile's top-left, recoloured white, or the icon-on
//   colour when the tile is on; the label and sub-label sit bottom-left in the 14 px
//   default font, black when on; a white overlay at the tile brightness (off / on %)
//   lights the tile; up/down and previous/next controls take the right half.
// Plain web component, no build step and no outside libraries.

const WS_PANELS = "oxrs_touchpanel/editor/panels";
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

class OxrsPanelEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._panels = [];
    this._panelIndex = 0;
    this._screenIndex = 0;
    this._selected = null; // {kind: "tile"|"empty", screen, tile}
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
    if (first) {
      this._fetch();
    } else if (this._entitiesChanged()) {
      this._scheduleFetch();
    }
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
    this._render();
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
    try {
      const result = await this._hass.callWS({ type: WS_PANELS });
      this._panels = result.panels || [];
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._loading = false;
    this._watched = new Map();
    for (const panel of this._panels) {
      for (const entityId of panel.entities || []) {
        this._watched.set(entityId, this._hass.states[entityId]);
      }
    }
    if (this._panelIndex >= this._panels.length) this._panelIndex = 0;
    this._render();
  }

  get _panel() {
    return this._panels[this._panelIndex];
  }

  _screens(panel) {
    return (panel?.conf?.screens || []).slice().sort((a, b) => a.screen - b.screen);
  }

  // ── rendering ─────────────────────────────────────────────────────────
  _render() {
    const root = this.shadowRoot;
    root.replaceChildren();
    root.append(el("style", {}, STYLE));

    this._menuButton = document.createElement("ha-menu-button");
    this._menuButton.hass = this._hass;
    this._menuButton.narrow = this._narrow;

    const panel = this._panel;
    const header = el(
      "div",
      { class: "toolbar" },
      this._menuButton,
      el("div", { class: "title" }, "OXRS panels"),
      this._panels.length > 1
        ? el(
            "select",
            {
              "aria-label": "Panel",
              onchange: (e) => {
                this._panelIndex = Number(e.target.value);
                this._screenIndex = 0;
                this._selected = null;
                this._render();
              },
            },
            this._panels.map((p, i) =>
              el("option", { value: i, selected: i === this._panelIndex }, p.title)
            )
          )
        : null
    );
    root.append(header);

    if (this._loading) {
      root.append(el("div", { class: "message" }, "Loading panels..."));
      return;
    }
    if (this._error) {
      root.append(el("div", { class: "message error" }, `Couldn't load the panels: ${this._error}`));
      return;
    }
    if (!panel) {
      root.append(el("div", { class: "message" }, "No OXRS panel is set up and running."));
      return;
    }

    const screens = this._screens(panel);
    if (this._screenIndex >= screens.length) this._screenIndex = 0;

    const grid = panel.grid || { cols: 3, rows: 3 };
    const info = el(
      "div",
      { class: "info" },
      el("span", { class: "name" }, panel.title),
      el("span", {}, `${panel.hardware || "board not reported"} · ${grid.cols} × ${grid.rows} tiles`),
      panel.available ? null : el("span", { class: "offline" }, "offline")
    );

    const track = el("div", { class: "track" });
    track.addEventListener("scroll", () => this._onScroll(track), { passive: true });
    for (const screen of screens) track.append(this._slide(panel, screen));
    if (!screens.length) {
      track.append(el("div", { class: "message" }, "This panel has no tiles yet. Add one in the panel's Configure dialog."));
    }

    const nav = screens.length > 1
      ? el(
          "div",
          { class: "nav" },
          el("button", { class: "arrow", "aria-label": "Previous screen", onclick: () => this._go(-1) }, "‹"),
          el(
            "div",
            { class: "dots" },
            screens.map((s, i) =>
              el("button", {
                class: i === this._screenIndex ? "dot on" : "dot",
                "aria-label": `Screen ${s.screen}`,
                onclick: () => this._goTo(i),
              })
            )
          ),
          el("button", { class: "arrow", "aria-label": "Next screen", onclick: () => this._go(1) }, "›")
        )
      : null;

    const main = el(
      "div",
      { class: "main" },
      el("div", { class: "stage" }, info, track, nav),
      this._sheet(panel)
    );
    root.append(main);

    this._track = track;
    this._resize.disconnect();
    this._resize.observe(track);
    requestAnimationFrame(() => {
      this._fit();
      track.scrollLeft = this._screenIndex * track.clientWidth;
    });
  }

  _go(step) {
    const count = this._screens(this._panel).length;
    if (!count) return;
    this._goTo((this._screenIndex + step + count) % count);
  }

  _goTo(index) {
    this._screenIndex = index;
    this._selected = null;
    this._track?.scrollTo({ left: index * this._track.clientWidth, behavior: "smooth" });
    this._updateDots();
    this._renderSheet();
  }

  _onScroll(track) {
    const index = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
    if (index !== this._screenIndex) {
      this._screenIndex = index;
      this._selected = null;
      this._updateDots();
      this._renderSheet();
    }
  }

  _updateDots() {
    this.shadowRoot.querySelectorAll(".dot").forEach((dot, i) => {
      dot.className = i === this._screenIndex ? "dot on" : "dot";
    });
  }

  // Scale every screen, drawn at the panel's own pixel size, to the width available.
  _fit() {
    const track = this._track;
    const panel = this._panel;
    if (!track || !panel) return;
    const { width, height } = panel.screen_px;
    const available = Math.min(track.clientWidth - 16, 560);
    const scale = Math.max(0.3, available / width);
    track.querySelectorAll(".frame").forEach((frame) => {
      frame.style.width = `${width * scale}px`;
      frame.style.height = `${height * scale}px`;
      frame.firstElementChild.style.transform = `scale(${scale})`;
    });
  }

  _slide(panel, screen) {
    const { width, height } = panel.screen_px;
    const cols = panel.grid.cols, rows = panel.grid.rows;
    const footer = panel.footer_px, pad = panel.padding_px;
    const cellW = Math.floor(width / cols);
    const cellH = Math.floor((height - footer) / rows);
    const bg = colour(screen.backgroundColorRgb) || colour(panel.conf.backgroundColorRgb) || "#000";

    const canvas = el("div", {
      class: "screen",
      style: { width: `${width}px`, height: `${height}px`, background: bg },
    });

    const covered = new Set();
    for (const tile of screen.tiles || []) {
      const w = tile.span?.right || 1, h = tile.span?.down || 1;
      const row = Math.floor((tile.tile - 1) / cols), col = (tile.tile - 1) % cols;
      if (row >= rows) continue;
      for (let dy = 0; dy < h; dy++) for (let dx = 0; dx < w; dx++) covered.add((row + dy) * cols + col + dx + 1);
      canvas.append(this._tile(panel, screen, tile, {
        left: col * cellW + pad,
        top: row * cellH + pad,
        width: cellW * w - 2 * pad,
        height: cellH * h - 2 * pad,
      }));
    }
    for (let position = 1; position <= cols * rows; position++) {
      if (covered.has(position)) continue;
      const row = Math.floor((position - 1) / cols), col = (position - 1) % cols;
      const selected = this._selected?.kind === "empty" && this._selected.screen === screen.screen && this._selected.tile === position;
      canvas.append(el(
        "button",
        {
          class: selected ? "empty selected" : "empty",
          style: { left: `${col * cellW + pad}px`, top: `${row * cellH + pad}px`, width: `${cellW - 2 * pad}px`, height: `${cellH - 2 * pad}px` },
          "aria-label": `Empty position ${position}`,
          onclick: () => this._select({ kind: "empty", screen: screen.screen, tile: position }),
        },
        el("span", {}, String(position))
      ));
    }

    // The firmware's footer: home and settings buttons either side of the screen's label.
    canvas.append(el(
      "div",
      { class: "footer", style: { height: `${footer}px` } },
      el("span", { class: "footer-icon" }, "⌂"),
      el("span", { class: "footer-label" }, screen.label || ""),
      el("span", { class: "footer-icon" }, "⚙")
    ));

    return el(
      "div",
      { class: "slide" },
      el("div", { class: "screen-name" }, screen.label || `Screen ${screen.screen}`, el("span", {}, ` · screen ${screen.screen}`)),
      el("div", { class: "frame" }, canvas)
    );
  }

  _state(panel, screen, tile) {
    return (panel.states || []).find((s) => s.screen === screen && s.tile === tile) || {};
  }

  _tile(panel, screen, tileConf, box) {
    const state = this._state(panel, screen.screen, tileConf.tile);
    const on = state.state === "on";
    const conf = panel.conf;
    const iconColour = colour(conf.iconOnColorRgb) || colour(DEFAULT_ICON_ON);
    const fg = on ? iconColour : "#fff";
    const textColour = on ? "#000" : "#fff";
    const brightness = on ? conf.tileBrightnessOn ?? 100 : conf.tileBrightnessOff ?? 10;
    const selected = this._selected?.kind === "tile" && this._selected.screen === screen.screen && this._selected.tile === tileConf.tile;

    const node = el("button", {
      class: selected ? "tile selected" : "tile",
      style: { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` },
      "aria-label": tileConf.label || `Tile ${tileConf.tile}`,
      "data-tile": String(tileConf.tile),
      onclick: () => this._select({ kind: "tile", screen: screen.screen, tile: tileConf.tile }),
    });

    // Own colour (a later state command wins over the config), else the screen shows through.
    const own = colour(state.backgroundColorRgb) || colour(tileConf.backgroundColorRgb);
    if (own) node.append(el("div", { class: "layer", style: { background: own } }));

    // The tile's white light, its brightness in the off or on state. It is the button's
    // own background in the firmware, so the background image and content sit above it.
    node.append(el("div", { class: "layer", style: { background: "#fff", opacity: String(brightness / 100) } }));

    // Background image: a library image at its own size times the zoom, centred;
    // album art is not kept after it is sent, so the player's cover stands in for it.
    const image = state.backgroundImage;
    if (image && image.name) {
      const art = panel.art?.[`${screen.screen}/${tileConf.tile}`];
      const src = panel.images?.[image.name] || art;
      if (src) {
        const img = el("img", { class: art && !panel.images?.[image.name] ? "art" : "bgimg", src, alt: "" });
        if (!img.classList.contains("art")) {
          const zoom = (Number(image.zoom) || 100) / 100;
          img.style.transform = `translate(-50%, -50%) scale(${zoom})`;
        }
        node.append(el("div", { class: "layer clip" }, img));
      }
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
    } else if (text) {
      // Non-empty text replaces the icon; a single space hides it (album art tiles).
      if (text.trim()) node.append(el("div", { class: "icontext", style: { color: on ? iconColour : "#fff" } }, text));
    } else {
      const iconName = state.icon || tileConf.icon;
      const src = BUILTIN_ICONS.has(iconName)
        ? `${this._static}/icons/${iconName}.png`
        : panel.icons?.[iconName];
      if (src) {
        node.append(el("div", {
          class: "icon",
          style: { background: fg, maskImage: `url("${src}")`, webkitMaskImage: `url("${src}")` },
        }));
      }
    }

    const controls = CONTROLS[tileConf.style];
    if (controls) {
      for (const [i, name] of controls.entries()) {
        const src = `${this._static}/icons/${name}.png`;
        node.append(el("div", {
          class: "control",
          style: {
            top: i === 0 ? "0" : "50%",
            background: fg,
            maskImage: `url("${src}")`,
            webkitMaskImage: `url("${src}")`,
          },
        }));
      }
    }

    const label = state.label ?? tileConf.label;
    if (label) node.append(el("div", { class: "label", style: { color: textColour } }, label));
    if (state.subLabel) node.append(el("div", { class: "sublabel", style: { color: textColour } }, state.subLabel));
    return node;
  }

  // ── side sheet ────────────────────────────────────────────────────────
  _select(selection) {
    this._selected = selection;
    this.shadowRoot.querySelectorAll(".tile.selected, .empty.selected").forEach((n) => n.classList.remove("selected"));
    this._renderSheet();
    this._markSelected();
  }

  _markSelected() {
    // Rendering the whole page again would reset the scroll; mark the cell in place.
    const panel = this._panel;
    if (!panel || !this._selected) return;
    const slide = this.shadowRoot.querySelectorAll(".slide")[this._screenIndex];
    if (!slide) return;
    const label = this._selected.kind === "empty" ? `Empty position ${this._selected.tile}` : null;
    for (const node of slide.querySelectorAll(".tile, .empty")) {
      const match = label
        ? node.getAttribute("aria-label") === label
        : node.classList.contains("tile") && node.dataset.tile === String(this._selected.tile);
      if (match) node.classList.add("selected");
    }
  }

  _renderSheet() {
    const old = this.shadowRoot.querySelector(".sheet");
    if (old) old.replaceWith(this._sheet(this._panel));
  }

  _sheet(panel) {
    const sheet = el("div", { class: "sheet" });
    const selection = this._selected;
    if (!panel || !selection) {
      sheet.append(el("p", { class: "hint" }, "Tap a tile to see what it is. Swipe, or use the arrows, to change screens."));
      return sheet;
    }
    if (selection.kind === "empty") {
      sheet.append(
        el("h3", {}, `Empty · position ${selection.tile}`),
        el("p", { class: "hint" }, "Adding a tile here from this page comes in the next step. For now, add it in the panel's Configure dialog.")
      );
      return sheet;
    }
    const meta = (panel.tiles || []).find((t) => t.screen === selection.screen && t.tile === selection.tile);
    const state = this._state(panel, selection.screen, selection.tile);
    if (!meta) {
      sheet.append(el("p", { class: "hint" }, "This tile is no longer in the configuration."));
      return sheet;
    }
    const entity = meta.entity_id ? this._hass?.states[meta.entity_id] : null;
    const rows = [
      ["Type", meta.type_label],
      ["Entity", entity ? `${entity.attributes.friendly_name || meta.entity_id} (${meta.entity_id})` : meta.entity_id || "none"],
      ["State", entity ? entity.state : state.state || "-"],
      ["Position", `${meta.tile} on screen ${meta.screen}`],
      ["Size", `${meta.size[0]} × ${meta.size[1]}`],
    ];
    if (meta.album_art) rows.push(["Album art", "shown"]);
    sheet.append(
      el("h3", {}, meta.label || meta.type_label),
      el("dl", {}, rows.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
      el("p", { class: "hint" }, "Editing and removing tiles from this page come in the next step. For now, use the panel's Configure dialog.")
    );
    return sheet;
  }
}

const STYLE = `
:host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
.toolbar { display: flex; align-items: center; gap: 8px; height: 56px; padding: 0 12px; border-bottom: 1px solid var(--divider-color); background: var(--app-header-background-color, var(--primary-background-color)); color: var(--app-header-text-color, var(--primary-text-color)); }
.title { font-size: 20px; flex: 1; }
:host(:not([narrow])) ha-menu-button { display: none; }
select { font: inherit; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
.message { padding: 24px; color: var(--secondary-text-color); }
.message.error { color: var(--error-color); }
.main { display: flex; gap: 16px; padding: 16px; align-items: flex-start; }
:host([narrow]) .main { flex-direction: column; align-items: stretch; padding: 8px; }
:host([narrow]) .stage { max-width: none; width: 100%; }
.stage { flex: 1; min-width: 0; max-width: 600px; }
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
.empty span { position: absolute; top: 6px; left: 8px; color: rgba(255,255,255,0.35); font-size: 14px; }
.empty:hover { border-color: rgba(255,255,255,0.45); }
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
.dots { display: flex; gap: 6px; }
.dot { width: 10px; height: 10px; border-radius: 50%; border: 0; padding: 0; background: var(--divider-color); cursor: pointer; }
.dot.on { background: var(--primary-color); }
.sheet { width: 300px; flex: 0 0 auto; background: var(--card-background-color); border-radius: 12px; padding: 16px; box-sizing: border-box; box-shadow: var(--ha-card-box-shadow, 0 0 0 1px var(--divider-color)); }
:host([narrow]) .sheet { width: 100%; }
.sheet h3 { margin: 0 0 12px; font-size: 18px; font-weight: 500; }
.sheet dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin: 0 0 12px; font-size: 14px; }
.sheet dt { color: var(--secondary-text-color); }
.sheet dd { margin: 0; word-break: break-word; }
.hint { color: var(--secondary-text-color); font-size: 14px; margin: 0; }
`;

customElements.define("oxrs-panel-editor", OxrsPanelEditor);
