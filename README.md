# OXRS Touch — Home Assistant Custom Component

A Home Assistant custom component for integrating and configuring [OXRS](https://oxrs.io/)-based touch panels with Home Assistant.

## Installation

**With HACS:** add this repository under **HACS → ⋮ → Custom repositories** (category *Integration*), then
download **OXRS Touch Panel**. Each release is a tag, so an older version can be picked with **Redownload**.

**By hand:**

1. Clone or download this repository (or a tagged release)
2. Copy the `custom_components/oxrs_touchpanel` folder to your Home Assistant config directory, so it ends up at:
   ```
   <config>/custom_components/oxrs_touchpanel
   ```
   Delete any previous copy first, so no stale files are left behind.

Then, either way:

1. Restart Home Assistant
2. Navigate to **Settings** → **Devices & Services** → **Integrations**
3. Click **Create Integration** and search for **OXRS Touch Panel**

## Configuration

### Prerequisites

- An OXRS device running the OXRS firmware (e.g., [WT32-SC01 PLUS](https://github.com/oxrs-io/oxrs-io))
- The device should be accessible on your local network
- MQTT broker configured in Home Assistant (required for communication)

### Setup via UI

1. Go to **Settings** → **Devices & Services** → **Integrations**
2. Click **Create Integration** → **OXRS Touch Panel**
3. Enter the required information:
   - **MQTT Topic**: Base topic for MQTT communication (typically `oxrs/[device-id]`)
   - **Device Name**: A friendly name for your panel

## The OXRS panels page

Once a panel is set up, an **OXRS panels** entry appears in the sidebar (for
administrators). It draws each panel's screens as the panel shows them - icons, colours,
on/off state, labels and album art - and lets you change them:

- Swipe, or use the arrows, to move between screens. The last screen is a new, empty one.
- Tap an empty space to add a tile there, choosing its type, entity, label, icon and size.
- Tap a tile to edit, move or remove it.
- Drag a tile to move it (on a phone, press and hold it first). Dropping it on a tile of the
  same size swaps the two.
- To move a tile to another screen, drag it over the arrows (or past the side of the screen)
  and hold it there until the screen changes, or tap **Move**, change screens, then tap
  where it should go. Moving a tile to the last, new screen creates that screen; a screen
  left without tiles is taken off the panel.
- Tap a screen's name to rename it or give it its own background colour.
- Tap **Panel settings** for the panel's timeouts, tile brightness, sensor interval,
  temperature correction, background colour, icon colour and album art sizing. The
  preview shows the colours and brightness before you apply them.
- Tap a tile, then **Add to favourites**, to keep it for any panel. Tapping an empty
  space offers your favourites and the tile setups you use most (the same kind of
  tile, icon, size and colours, whatever it is bound to), ready to place.
- A panel Home Assistant finds on MQTT shows at the top of the page, with **Add panel**.
- Tap **Device** to see the panel's board, address and firmware, restart it, update
  its firmware, or delete the panel. Updates come from the official OXRS firmware releases on GitHub, built
  for the panel's own board and connection; Home Assistant also offers the latest
  stable release as a firmware update on the panel's device page.
- Tap **Library** to see the background images and custom icons every panel shares,
  and which tiles use each. Add one by choosing a file (or pasting the OXRS Asset
  Generator's base64), or delete one.

Changes are staged until you press **Apply to panel**, which sends them to the panel in
one go (or **Discard** to drop them). The library is the exception: it is shared by
every panel, so adding or deleting there happens straight away, as in the dialog.

## Support

- **Issues & Bug Reports** — [GitHub Issues](https://github.com/nelferr/oxrs_touch-homeassistant-custom-component/issues)
- **Home Assistant Community** — [Discussion Forums](https://community.home-assistant.io/)
- **OXRS Project** — [OXRS Documentation](https://oxrs.io/)

## Disclaimer

This is an AI developed integration and is not officially affiliated with Home Assistant or the OXRS project. Use at your own risk.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-%E2%89%A52024.6-blue.svg)](https://www.home-assistant.io/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

## Changelog

### Version 1.0.0 (Initial Release)
- Initial release with support for basic entity types
---

**Need help?** Check the [Home Assistant Community](https://community.home-assistant.io/) or open an issue on GitHub.
