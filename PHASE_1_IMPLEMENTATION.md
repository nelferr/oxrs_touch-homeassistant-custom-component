# OXRS Touch Panel - Phase 1 Implementation

## What's New

### OxrsTileAction Class (models.py)

A new flexible action system that allows tiles to execute any Home Assistant service sequence with:
- Multiple service calls in a single action
- Templating support (access event data via `{{ variables }}`)
- Conditional execution (if/then logic)
- Delays and timing control
- Full Home Assistant Script validation

### New Configuration Keys (const.py)

```python
CONF_ACTIONS = "actions"              # List of actions per tile
CONF_ACTION_SEQUENCE = "sequence"     # HA service sequence
CONF_ACTION_MODE = "mode"             # Script execution mode (single/parallel/queued)
CONF_ACTION_CONDITIONS = "conditions" # Optional conditions
```

## Architecture

```
MQTT stat/ message from panel
    ↓
hub._on_stat() receives event
    ↓
Tile identified (screen, tile number)
    ↓
For each OxrsTileAction in tile.actions:
    ↓
action.run(data=event_data)
    ↓
Script object executes full sequence
    ↓
Service calls, delays, templates, etc.
```

## Backward Compatibility

✅ **Existing tile system remains unchanged**
- Old hardcoded tile types (cct, slider, button, etc.) still work
- Can coexist with new flexible actions during migration
- No breaking changes to current configuration

## How to Use OxrsTileAction

### Example 1: Simple Service Call

```python
from oxrs_touchpanel.models import OxrsTileAction

action = OxrsTileAction(
    hass=hass,
    config={
        "mode": "single",
        "sequence": [
            {
                "service": "light.turn_on",
                "data": {"entity_id": "light.bedroom"}
            }
        ]
    },
    tile_id="B3_1_1"
)

# Execute when button is pressed
await action.run(data={"payload": incoming_mqtt_data})
```

### Example 2: Multi-Service Sequence

```python
action = OxrsTileAction(
    hass=hass,
    config={
        "mode": "single",
        "sequence": [
            # Turn on light
            {
                "service": "light.turn_on",
                "data": {
                    "entity_id": "light.bedroom",
                    "brightness_pct": 100
                }
            },
            # Wait 500ms
            {"delay": {"milliseconds": 500}},
            # Activate scene
            {
                "service": "scene.turn_on",
                "data": {"entity_id": "scene.movie_mode"}
            }
        ]
    }
)
```

### Example 3: With Templates

```python
action = OxrsTileAction(
    hass=hass,
    config={
        "mode": "single",
        "sequence": [
            {
                "service": "light.turn_on",
                "data": {
                    "entity_id": "light.bedroom",
                    # Access variables from run_variables
                    "brightness_pct": "{{ payload.brightness }}",
                    "color_temp_kelvin": "{{ payload.kelvin }}"
                }
            }
        ]
    }
)

# When calling, pass the data as run_variables
await action.run(data={
    "payload": {
        "brightness": 75,
        "kelvin": 4000
    }
})
```

## Integration with Hub

### Current (Phase 1)
The hub still uses hardcoded tile types via `tiles.py`. The new `OxrsTileAction` is available as an optional alternative.

### Next (Phase 2)
Config flow will allow users to define action sequences instead of picking tile types.

### Example Integration

In `hub.py`, you could add:

```python
# Listen for tile press events
@callback
def _on_stat(self, msg: mqtt.ReceiveMessage) -> None:
    payload = json.loads(msg.payload)
    screen = payload.get("screen")
    tile = payload.get("tile")
    
    # Find tile
    tile_config = self._find_tile(screen, tile)
    
    # NEW: If tile has actions, run them
    if hasattr(tile_config, 'actions'):
        for action in tile_config.actions:
            self.hass.async_create_task(
                action.run(data={"payload": payload})
            )
```

## Script Modes Explained

- **`single`**: Only one execution at a time (default)
  - If triggered again while running, new execution waits
  - Useful for: most actions
  
- **`parallel`**: Multiple executions run simultaneously
  - Triggers run independently
  - Useful for: independent actions (volume up/down)
  
- **`queued`**: Executions queue up
  - Each waits for previous to complete
  - Useful for: critical sequences
  
- **`restart`**: Restarts if triggered while running
  - Useful for: interrupt-able actions

## Logging

Enable debug logging to see action execution:

```yaml
logger:
  logs:
    oxrs_touchpanel.models: DEBUG
    homeassistant.helpers.script: DEBUG
```

Output:
```
DEBUG (MainThread) [oxrs_touchpanel.models] Initialized action: oxrs_touchpanel_action_B3_1_1
DEBUG (MainThread) [oxrs_touchpanel.models] Running action sequence: B3_1_1
```

## Next Steps (Phase 2)

1. **Config UI Updates**
   - Redesign add_tile flow to support actions
   - Add YAML editor or action builder UI
   - Allow multiple actions per tile

2. **Migration**
   - Auto-migrate old tile configs to action-based
   - Maintain backward compatibility

3. **Testing**
   - Unit tests for OxrsTileAction
   - Integration tests with hub

## Testing Phase 1

### Unit Test Example

```python
async def test_oxrs_tile_action_initialization(hass):
    """Test OxrsTileAction initialization."""
    action = OxrsTileAction(
        hass=hass,
        config={
            "mode": "single",
            "sequence": [
                {"service": "light.turn_on",
                 "data": {"entity_id": "light.test"}}
            ]
        }
    )
    
    # Wait for async initialization
    await asyncio.sleep(0.1)
    
    assert action.active is True
    assert action.script is not None
    assert action.mode == "single"
```

## Files Changed

1. **oxrs_touchpanel/const.py**
   - Added CONF_ACTIONS, CONF_ACTION_SEQUENCE, CONF_ACTION_MODE, CONF_ACTION_CONDITIONS

2. **oxrs_touchpanel/models.py** (NEW)
   - OxrsTileAction class with Script integration
   - Full docstrings and type hints

3. **oxrs_touchpanel/__init__.py**
   - Added import for models
   - Added Phase 1-4 roadmap documentation

## No Breaking Changes

✅ Existing configuration still works  
✅ Hardcoded tile types still function  
✅ MQTT communication unchanged  
✅ No updates required to existing setups

## Feedback & Issues

Phase 1 is the foundation. If you encounter:
- **Import errors**: Check Python version (3.9+)
- **Script validation errors**: Check sequence YAML syntax against HA docs
- **Templates not rendering**: Check variable names match run_variables keys

## Related Documentation

- [OXRS Technical Review](../OXRS_TOUCH_TECHNICAL_REVIEW.md)
- [Refactoring Guide](../OXRS_REFACTOR_FLEXIBLE_ACTIONS.md)
- [Home Assistant Script Documentation](https://www.home-assistant.io/docs/automation/action/)
- [Template Documentation](https://www.home-assistant.io/docs/configuration/templating/)
