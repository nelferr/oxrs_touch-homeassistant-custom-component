"""Custom icon registry for OXRS Touch Panel.

Icons are organized by category and sent to the panel as addIcon commands
on startup (alongside addImage for background images). They are not persistent
and must be re-sent after every panel restart.

OXRS addIcon rules:
  - PNG format only
  - Name CANNOT start with underscore (reserved for firmware built-ins)
  - Optimal size: 60×60 px, max ~140×140 px
  - Max size: ~4KB encoded
  - Names are case-sensitive and must be unique

To add new icons:
  1. Generate base64 PNG using https://oxrs.io/tools/asset-generator.html
     (change 'addImage' to 'addIcon' in the output JSON)
  2. Add entry to the relevant category below: "name": "base64string"
  3. Name appears in the icon selector under its category group
  4. Icon is sent to panel automatically on startup

CATEGORIES:
  LIGHTING    - lights, scenes, bulbs, LED strips
  CLIMATE     - heating, cooling, fans, ventilation
  SECURITY    - locks, alarms, cameras, motion
  AV          - TV, speakers, projectors, media
  APPLIANCES  - kitchen, laundry, home appliances
  ENERGY      - solar, battery, power, EV
  OUTDOOR     - garden, pool, irrigation, gates
  MISC        - anything that doesn't fit above
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Category display labels (used in the icon selector dropdown)
# ---------------------------------------------------------------------------
ICON_CATEGORIES: dict[str, str] = {
    "lighting":   "Lighting",
    "climate":    "Climate",
    "security":   "Security",
    "av":         "Audio / Video",
    "appliances": "Appliances",
    "energy":     "Energy",
    "outdoor":    "Outdoor",
    "misc":       "Miscellaneous",
}

# ---------------------------------------------------------------------------
# Custom icons by category
# dict key  = OXRS icon name (used in conf/ and addIcon command)
# dict value = base64-encoded PNG string (added by Nelson)
# ---------------------------------------------------------------------------

CUSTOM_ICONS: dict[str, dict[str, str]] = {
    "lighting": {
        # Example entry (replace with real base64):
        # "lightstrip":  "iVBORw0KGgoAAAANSUhEUg...",
        # "scene":       "iVBORw0KGgoAAAANSUhEUg...",
        # "dimmer":      "iVBORw0KGgoAAAANSUhEUg...",
    },
    "climate": {
        # "heatpump":    "iVBORw0KGgoAAAANSUhEUg...",
        # "radiator":    "iVBORw0KGgoAAAANSUhEUg...",
        # "ventilation": "iVBORw0KGgoAAAANSUhEUg...",
        # "aircon":      "iVBORw0KGgoAAAANSUhEUg...",
    },
    "security": {
        # "camera":      "iVBORw0KGgoAAAANSUhEUg...",
        # "motion":      "iVBORw0KGgoAAAANSUhEUg...",
        # "alarm":       "iVBORw0KGgoAAAANSUhEUg...",
        # "siren":       "iVBORw0KGgoAAAANSUhEUg...",
    },
    "av": {
        # "tv":          "iVBORw0KGgoAAAANSUhEUg...",
        # "projector":   "iVBORw0KGgoAAAANSUhEUg...",
        # "subwoofer":   "iVBORw0KGgoAAAANSUhEUg...",
        # "headphones":  "iVBORw0KGgoAAAANSUhEUg...",
    },
    "appliances": {
        # "washing":     "iVBORw0KGgoAAAANSUhEUg...",
        # "dryer":       "iVBORw0KGgoAAAANSUhEUg...",
        # "dishwasher":  "iVBORw0KGgoAAAANSUhEUg...",
        # "oven":        "iVBORw0KGgoAAAANSUhEUg...",
        # "fridge":      "iVBORw0KGgoAAAANSUhEUg...",
    },
    "energy": {
        # "solar":       "iVBORw0KGgoAAAANSUhEUg...",
        # "battery":     "iVBORw0KGgoAAAANSUhEUg...",
        # "ev":          "iVBORw0KGgoAAAANSUhEUg...",
        # "meter":       "iVBORw0KGgoAAAANSUhEUg...",
    },
    "outdoor": {
        # "pool":        "iVBORw0KGgoAAAANSUhEUg...",
        # "gate":        "iVBORw0KGgoAAAANSUhEUg...",
        # "irrigation":  "iVBORw0KGgoAAAANSUhEUg...",
        # "garden":      "iVBORw0KGgoAAAANSUhEUg...",
    },
    "misc": {
        # "robot":       "iVBORw0KGgoAAAANSUhEUg...",
        # "phone":       "iVBORw0KGgoAAAANSUhEUg...",
        # "bell":        "iVBORw0KGgoAAAANSUhEUg...",
    },
}


def all_custom_icon_names() -> list[str]:
    """Return flat list of all custom icon names across all categories."""
    return [
        name
        for category in CUSTOM_ICONS.values()
        for name in category
    ]


def get_icon_selector_options() -> list[dict]:
    """Build options list for HA SelectSelector, grouped by category.

    Returns a flat list of {"value": name, "label": label} dicts.
    Built-in firmware icons (prefixed with _) come first as a group,
    then custom icons grouped by category.

    The HA SelectSelector renders these as a flat dropdown — group headers
    are emitted as disabled separator entries so the user can visually
    scan categories without confusion.
    """
    from .const import BUILTIN_ICONS

    options: list[dict] = []

    # ── Built-in firmware icons ──────────────────────────────────────────────
    options.append({"value": "__group_builtin__", "label": "── Built-in icons ──", "disabled": True})
    for name in BUILTIN_ICONS:
        options.append({"value": name, "label": name})

    # ── Custom icons by category ─────────────────────────────────────────────
    for category_key, category_label in ICON_CATEGORIES.items():
        icons = CUSTOM_ICONS.get(category_key, {})
        if not icons:
            continue  # skip empty categories
        options.append({
            "value": f"__group_{category_key}__",
            "label": f"── {category_label} ──",
            "disabled": True,
        })
        for name in icons:
            options.append({"value": name, "label": name})

    return options
