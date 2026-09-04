# OXRS Touch — Home Assistant Custom Component

A Home Assistant custom component for integrating and configuring [OXRS](https://oxrs.io/)-based touch panels with Home Assistant.

## Installation

1. Clone or download this repository
2. Copy the `oxrs_touchpanel` folder to your Home Assistant config directory:
   ```
   ~/.homeassistant/custom_components/
   ```
3. Restart Home Assistant
4. Navigate to **Settings** → **Devices & Services** → **Integrations**
5. Click **Create Integration** and search for **OXRS Touch Panel**

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
