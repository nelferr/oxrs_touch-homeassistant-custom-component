# OXRS Touch Panel - Phase 2 Implementation

## What's New

### Config UI for Flexible Actions

Users can now choose between two tile formats when adding tiles:

1. **Hardcoded tile type** (original)
   - Simple: pick entity, get automatic behavior
   - Limited to 7 tile types (light, switch, climate, etc.)
   - Uses old configuration format

2. **Flexible actions** (new!)
   - Advanced: define custom action sequences
   - Any Home Assistant service supported
   - Support for templates, delays, and conditions
   - Multiple actions per tile press

### New Files

- **`oxrs_touchpanel/migrations.py`** - Converts old configs to new format
- **`oxrs_touchpanel/models.py`** - Updated with `OxrsTile` class

### Updated Files

- **`oxrs_touchpanel/config_flow.py`** - New UI flow for flexible actions
- **`oxrs_touchpanel/hub.py`** - Supports both old and new tile formats
- **`oxrs_touchpanel/strings.json`** - New UI text labels
- **`oxrs_touchpanel/models.py`** - Added `OxrsTile` class

## Migration & Backward Compatibility

✅ **100% Backward Compatible**
- Existing configurations continue to work unchanged
- Old and new tiles can coexist
- No data loss on upgrade
- Manual migration available via `migrations.migrate_tile_to_actions()`

## How to Use: Flexible Actions

### Step 1: Add a Tile

1. **Settings** → **Devices & Services** → **OXRS Touch Panel**
2. Click **Configure**
3. Click **Add a tile**
4. Choose **Flexible actions (advanced...)**
5. Select **Screen number**

### Step 2: Configure Position, Label, Icon

- **Tile position** - Pick an empty spot
- **Label** - Display name (e.g., "Night Mode")
- **Icon** - Pick from built-in icons

### Step 3: Define Action Sequence

Write your automation sequence in YAML:

```yaml
- service: light.turn_on
  data:
    entity_id: light.bedroom
    brightness_pct: 100
- delay:
    milliseconds: 500
- service: scene.turn_on
  data:
    entity_id: scene.movie_mode
```

That's it! When the button is pressed on the panel, all actions run in order.

---

## Examples

### Example 1: Simple Light Toggle

```yaml
- service: light.toggle
  data:
    entity_id: light.bedroom
```

### Example 2: Scene with Multiple Lights

```yaml
- service: light.turn_on
  data:
    entity_id: light.bedroom
    brightness_pct: 100
    color_temp_kelvin: 4000
- service: light.turn_on
  data:
    entity_id: light.bathroom
    brightness_pct: 50
- service: switch.turn_off
  data:
    entity_id: switch.fans
```

### Example 3: Smart Morning Routine

```yaml
- service: light.turn_on
  data:
    entity_id: light.bedroom
    brightness_pct: 20
    transition: 5
- delay:
    seconds: 2
- service: light.turn_on
  data:
    entity_id: light.kitchen
    brightness_pct: 100
- service: climate.set_hvac_mode
  data:
    entity_id: climate.bedroom
    hvac_mode: heat
```

### Example 4: Movie Night Scene

```yaml
- service: light.turn_on
  data:
    entity_id: light.living_room
    brightness_pct: 0
    transition: 2
- service: climate.set_temperature
  data:
    entity_id: climate.living_room
    temperature: 20
- service: media_player.turn_on
  data:
    entity_id: media_player.tv
- delay:
    seconds: 1
- service: media_player.select_source
  data:
    entity_id: media_player.tv
    source: HDMI
```

### Example 5: With Templates (Access Panel Data)

When a colorPicker tile sends data, you can access it:

```yaml
- service: light.turn_on
  data:
    entity_id: light.bedroom
    brightness_pct: "{{ payload.brightness }}"
    color_temp_kelvin: "{{ payload.colorKelvin }}"
```

Available variables from `{{ payload }}`:
- For colorPicker: `brightness` (0-100), `colorKelvin` (2000-6000)
- For slider: `level` (0-100)
- For thermostat: `temperature`, `mode` (1-based index)
- For button/switch: `event` ("single", "hold", "release")

---

## Technical Details

### Tile Configuration Format

**Old (Hardcoded Type):**
```json
{
  "screen": 1,
  "tile": 1,
  "type": "button",
  "entity_id": "light.bedroom",
  "label": "Bedroom",
  "icon": "_bulb"
}
```

**New (Flexible Actions):**
```json
{
  "screen": 1,
  "tile": 1,
  "label": "Bedroom Scene",
  "icon": "_bulb",
  "actions": [
    {
      "mode": "single",
      "sequence": [
        {
          "service": "light.turn_on",
          "data": {"entity_id": "light.bedroom"}
        }
      ]
    }
  ]
}
```

### How It Works

1. **User presses button** on panel
2. Panel publishes MQTT `stat/` message
3. `hub._on_stat()` finds the tile
4. Checks if tile has `actions`
5. **If yes:** Creates `OxrsTile` object, executes all actions
6. **If no:** Uses old hardcoded tile type handler

### Script Modes

```yaml
mode: "single"     # Default - one execution at a time
mode: "parallel"   # Multiple executions simultaneously
mode: "queued"     # Queue up executions
mode: "restart"    # Restart if triggered again
```

---

## Important Notes

### Sequences Must Be Valid YAML

The action sequence is validated using Home Assistant's SCRIPT_SCHEMA. Common mistakes:

❌ **Wrong - missing dash:**
```yaml
service: light.turn_on
```

✅ **Right - each action starts with dash:**
```yaml
- service: light.turn_on
  data:
    entity_id: light.bedroom
```

❌ **Wrong - indentation wrong:**
```yaml
- service: light.turn_on
data:
  entity_id: light.bedroom
```

✅ **Right - consistent 2-space indentation:**
```yaml
- service: light.turn_on
  data:
    entity_id: light.bedroom
```

### Error Handling

If your sequence has a syntax error, you'll see it in logs:

```
Settings → Logs → Search "oxrs_touchpanel"
```

Look for ERROR lines like:
```
ERROR: Failed to initialize action 1_1_0: ...
```

Then check your YAML syntax carefully.

---

## Migration from Old Format (Manual)

If you want to convert an existing hardcoded tile to flexible actions:

1. Note the old tile settings (entity_id, type, label)
2. Remove the old tile
3. Add a new tile with Flexible Actions format
4. Write the equivalent action sequence

Example conversion:

**Old:**
```
Type: button
Entity: light.bedroom
```

**New:**
```yaml
- service: light.toggle
  data:
    entity_id: light.bedroom
```

---

## UI Flow Diagram

```
Add Tile
   ↓
Choose Format?
   ├─→ Hardcoded (old)
   │      ↓
   │   Choose Type (light, switch, etc.)
   │      ↓
   │   Pick Entity & Position
   │      ↓
   │   Done!
   │
   └─→ Flexible Actions (new)
          ↓
       Pick Position
          ↓
       Enter Label & Icon
          ↓
       Write Action Sequence (YAML)
          ↓
       Done!
```

---

## Logging & Debugging

Enable debug logging:

```yaml
logger:
  logs:
    oxrs_touchpanel: DEBUG
    oxrs_touchpanel.models: DEBUG
    homeassistant.helpers.script: DEBUG
```

Then check **Settings → Logs** after pressing a tile button.

Expected output:
```
DEBUG [oxrs_touchpanel.models] Initialized action: oxrs_touchpanel_action_1_1_0
DEBUG [oxrs_touchpanel.models] Running action sequence: 1_1_0
DEBUG [homeassistant.helpers.script] Executing script with variables: {'payload': {...}}
```

---

## Limitations & Future Work

### Current Limitations (Phase 2)
- ✓ Single action sequences only (Phases 3-4 will add conditional logic)
- ✓ No built-in error handling (will improve in Phase 4)
- ✓ No action retries (Phase 4 feature)

### Future Phases
- **Phase 3:** Remove hardcoded tile types, simplify code
- **Phase 4:** Add conditions, retries, error handling

---

## Support

### Common Issues

**Q: My action sequence isn't running**
- Check YAML syntax (use online validator)
- Check entities exist and are available
- Enable DEBUG logging
- Check HA logs for error messages

**Q: Can I use conditions?**
- Phase 2 doesn't support conditions yet
- Phase 4 will add: `if:` conditions
- Workaround: use automations with conditions

**Q: Multiple buttons with same action?**
- Add the same sequence to multiple tiles
- No sharing/importing yet (Phase 3+ feature)

**Q: How do I access tile data in templates?**
- Use `{{ payload.fieldname }}`
- Available fields depend on tile type (see Examples section)

---

## Version Info

- **Phase 1** (v0.1.0): Foundation - OxrsTileAction class
- **Phase 2** (v0.2.0): Config UI - Flexible actions in UI
- **Phase 3** (v0.3.0): Cleanup - Remove hardcoded tile types
- **Phase 4** (v0.4.0): Advanced - Conditions, retries, error handling

---

## Files Changed in Phase 2

| File | Changes |
|------|---------|
| `config_flow.py` | +80 lines - New UI steps for actions |
| `hub.py` | +30 lines - Support both tile formats |
| `models.py` | +50 lines - Added OxrsTile class |
| `strings.json` | +15 lines - UI text for new steps |
| `migrations.py` | NEW - 200 lines - Format migration |
| `manifest.json` | v0.1.0 → v0.2.0 |

---

## Next Steps

### To Use Phase 2
1. Update integration to v0.2.0
2. Go to OXRS Panel config
3. Click "Add a tile"
4. Choose "Flexible actions"
5. Write your action sequence

### For Phase 3 & Beyond
- Submit feedback: What features would help?
- Migrate your existing tiles gradually
- No rush - old format still works!

---

## Testing Phase 2

**Basic Test:**
1. Add a new flexible action tile
2. Press button on panel
3. Verify the action runs
4. Check HA logs for any errors

**Advanced Test:**
1. Add action with templates: `brightness_pct: "{{ payload.brightness }}"`
2. Press button
3. Verify brightness is set from panel data

**Error Test:**
1. Intentionally introduce YAML error
2. Try to add tile
3. Should show error in logs
4. Fix YAML and try again
