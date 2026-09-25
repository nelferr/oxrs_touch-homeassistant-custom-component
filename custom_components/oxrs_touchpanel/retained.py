"""Clean up the MQTT messages a removed panel leaves on the broker.

A panel publishes two RETAINED messages (OXRS-IO-MQTT-ESP32-LIB): its adopt
message on stat/<id>/adopt, and its online/offline state on stat/<id>/lwt.

The adopt message is what makes Home Assistant offer the panel for setup - the
manifest matches stat/+/adopt - and a retained message is redelivered every time
HA starts or MQTT reconnects. So a panel that has been removed, or was never
really there, is "discovered" again on every restart until that message is
cleared, even though nothing on the network is publishing it.

Deleting a retained message means publishing an empty retained payload to its
topic.

A panel that is still online is left alone. Its retained messages are not stale:
they are how it gets found again if the user deletes and re-adds it, and
clearing them would leave it un-discoverable until it next reconnects.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from .const import topic_adopt, topic_lwt

_LOGGER = logging.getLogger(__name__)

# A retained message is delivered as soon as we subscribe, so this only has to
# cover the broker's round trip. If nothing arrives, nothing is claiming the
# panel is online.
LWT_WAIT_SECONDS = 2.0


def payload_says_online(payload: Any) -> bool:
    """Whether an LWT payload reports the panel as online."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("online") is True


async def async_reported_online(hass: HomeAssistant, client_id: str) -> bool:
    """Whether the broker's last known state for this panel is "online".

    Reads the retained LWT message. The broker publishes a panel's "offline"
    will when it drops, so a retained "online" means it is (or was, very
    recently) connected.
    """
    seen: list[Any] = []
    arrived = asyncio.Event()

    @callback
    def _on_message(msg: Any) -> None:
        if not arrived.is_set():
            seen.append(msg.payload)
            arrived.set()

    unsubscribe = await mqtt.async_subscribe(hass, topic_lwt(client_id), _on_message)
    try:
        await asyncio.wait_for(arrived.wait(), timeout=LWT_WAIT_SECONDS)
    except asyncio.TimeoutError:
        return False
    finally:
        unsubscribe()
    return payload_says_online(seen[0])


async def async_clear_retained(hass: HomeAssistant, client_id: str) -> bool:
    """Delete a removed panel's retained messages, unless it is still online.

    Returns True if they were cleared. Raises if MQTT is unavailable; the caller
    decides what to do about that.
    """
    if await async_reported_online(hass, client_id):
        _LOGGER.info(
            "%s still reports itself online, so its retained MQTT messages were "
            "left in place - it will be offered for setup again",
            client_id,
        )
        return False

    for topic in (topic_adopt(client_id), topic_lwt(client_id)):
        await mqtt.async_publish(hass, topic, "", qos=0, retain=True)
    _LOGGER.info("Cleared the retained MQTT messages of removed panel %s", client_id)
    return True
