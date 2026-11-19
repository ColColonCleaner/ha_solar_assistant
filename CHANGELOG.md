## v0.3.1 (fork ColColonCleaner/ha_solar_assistant)

### Feat

- **topoff**: battery top-off from the grid when the batteries have not been full for a set number of days, off by default
- **number**: input boxes with a step from the unit and the decimals of the limits and the value (#21)
- **sensor**: a suggested display precision from the unit and the topic
- **time**: time settings as time entities, which take any minute (#28)

### Fix

- **sensor**: a value with its unit inside, such as "230 V", parses to its number (#31)
- **select**: a value outside the options stays visible instead of unknown (#34)
- **coordinator**: one write at a time, verified by the unit, with retries of a 422 (#32, #35)
- **options**: a submit without a section keeps that section (found in PR #27)

## v0.3.0 (2026-10-30)

### Feat

- **options**: make the unit's status metrics selectable
- **coordinator**: take the unit's system metrics from the metrics stream

### Fix

- **config-flow**: keep the unit's system metrics out of the options flow
- **coordinator**: trust SA's device_class on system metrics

## v0.2.0 (2026-07-02)

### Feat

- **recovery**: complete LAN IP-change recovery for local entries
- **sensor**: expose the unit's system metrics as diagnostics

## v0.1.0 (2026-06-12)

### Feat

- **config-flow**: name search for the cloud site picker

### Fix

- **sensor**: only create sensors for enabled metrics
- **binary_sensor**: name the connection entity via translation_key
- **discovery**: pass site_id/site_key on cloud REST discovery
