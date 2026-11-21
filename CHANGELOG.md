## v0.5.6 (fork ColColonCleaner/ha_solar_assistant)

### Feat

- **diagnostics**: the download lists the metrics of the group Ignore under `ignored_topics`, with their definition and value. The unit streams no update of them, so they get no entity, and the list shows which metric that is

## v0.5.5 (fork ColColonCleaner/ha_solar_assistant)

### Feat

- **options**: every metric is enabled by default, with the topic filter `*` that the WebSocket API documents, and `system/*`. Before, the default was the server's curated set of 10 globs. A metric deselected in the options gets no sensor. An entry with a saved selection keeps it
- **coordinator**: a settings topic joins the subscription only when no enabled glob covers it

### Fix

- **devices**: the metric "Connection type", such as Serial, no longer becomes the model of the device. "Model number" becomes the model ID, and a device without a model metric clears the model that v0.4.0 to v0.5.4 stored. "Serial number" and "Software version" or "Firmware" still give the serial number and the firmware

## v0.5.4 (fork ColColonCleaner/ha_solar_assistant)

### Feat

- **sensor**: a minute after the first discovery, each sensor takes over the entity ID of its SolarAssistant MQTT entity of the same topic, when that MQTT entity is deleted and its ID is free. The rename is the rename of the HA UI, so the recorder continues the history of the MQTT entity, and automations and dashboards that name the MQTT ID work again. Each topic is adopted once, so a later rename by hand holds

## v0.5.3 (fork ColColonCleaner/ha_solar_assistant)

### Fix

- **sensor**: v0.5.2 removed every total whose name an inverter definition also had, 16 on an EG4 12000XP, although 13 of those inverter metrics have no entity. So the battery state of charge, power, voltage, current, temperature, state of health, capacity and cell voltages, the grid voltage and frequency, and the AC output voltage and frequency lost their entity. Now only total/pv_power, total/load_power and total/grid_power are dropped, the totals whose values were compared, and only while the inverter metric of the same topic name has an entity. The 13 totals come back with their old entity IDs, because HA restores a deleted entity
- **devices**: a total on the inverter device reads "Total ..." only when an entity of the inverter has the same name. An inverter metric without an entity no longer counts

## v0.5.2 (fork ColColonCleaner/ha_solar_assistant)

### Fix

- **sensor**: on a unit with one inverter, a total that repeats an inverter metric of the same name, such as total/pv_power, total/load_power and total/grid_power, gets no entity, and the entity of an older version leaves the registry. On the EG4 12000XP every update of 24 hours agreed. So no sensor of the inverter device reads "Total ..." anymore

## v0.5.1 (fork ColColonCleaner/ha_solar_assistant)

### Fix

- **devices**: a minute after the first discovery, each device of the entry that holds no entity is removed, such as the Totals device after its sensors moved to the inverter (#19)
- **devices**: a total on the inverter device keeps its own name, such as "Battery temperature". Only a name that an inverter metric already has, such as "PV power", reads "Total PV power"

## v0.5.0 (fork ColColonCleaner/ha_solar_assistant)

### Feat

- **devices**: on a unit with one inverter, the totals sensors and the SOC spread join the inverter device, named "Total ...". With two or more inverters they keep the Totals device. Entity IDs stay
- **devices**: a device with no entity of the entry can be deleted in the UI, such as the emptied Totals device or a phantom device of a restart (#19)
- **coordinator**: the REST discovery stores every definition before it announces the new topics, so each entity sees the whole unit

- **topoff**: a due top-off starts after the battery has discharged for a set time without a break, 60 min by default, instead of in a time window. The grid then fills only the rest above the daily peak of the SOC. The number "Top-off after discharge" sets the time, the state attribute `discharging_since` shows the count, and the window entities leave the registry

## v0.4.2 (fork ColColonCleaner/ha_solar_assistant)

### Fix

- **topoff**: reverts v0.4.1. The whole-day slot starts at 00:00 again. On 2026-11-20 a top-off of 0.4.0 on an EG4 12000XP charged at +96.6 A with slot 1 from 00:00 to 23:59, so the report that a slot from 00:00 reads as no slot does not hold for this inverter. The earlier top-off that took no charge ran at 95% SOC, just after the batteries were full

## v0.4.1 (fork ColColonCleaner/ha_solar_assistant)

### Fix

- **topoff**: the whole-day grid-charge slot starts at 00:01, after a forum report on Luxpower and EG4 inverters. Reverted in v0.4.2, because the measurement disproved it for the EG4 12000XP

## v0.4.0 (fork ColColonCleaner/ha_solar_assistant)

### Feat

- **switch**: each select with the options Enabled and Disabled is a switch
- **services**: the actions set_setting (with verify), start_topoff and stop_topoff
- **diagnostics**: a download of every topic with its definition, options and value, credentials redacted
- **sensor**: Battery SOC spread, the largest pack SOC minus the smallest, and a Battery SOC spread alert
- **binary_sensor**: a Data sensor per inverter and battery, off when the device sent no changed value for a set time (#14)
- **options**: a minimum update interval per sensor, with an immediate update at a zero crossing (#24, #27), the stale time and the spread alert
- **config-flow**: zeroconf discovery, which also updates the IP of a known unit (#16)
- **repairs**: a repair issue when a write or a top-off fails
- **devices**: model, serial number and firmware from the Info metrics (#11)
- **entities**: entry.runtime_data, AddConfigEntryEntitiesCallback and translated names of the top-off entities

### Fix

- **topoff**: the result of a manual stop says "stopped by Stop top-off", not "finished"
- **number**: inverter_1/smart_load_start_pv_power is kW, which SolarAssistant labels W

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
