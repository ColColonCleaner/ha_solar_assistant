# SolarAssistant for Home Assistant

Official [Home Assistant](https://www.home-assistant.io) integration for [SolarAssistant](https://solar-assistant.io).

Connects your SolarAssistant unit to Home Assistant over the local network or through the SolarAssistant cloud. Exposes your solar system as
native entities: live telemetry sensors plus writable settings for inverter parameters - no separate MQTT broker required.

## This fork

This is a fork of [Solar-Assistant/ha_solar_assistant](https://github.com/Solar-Assistant/ha_solar_assistant). It adds:

- **Battery top-off**: a grid charge to full when the batteries have not been full for a set number of days. See [Battery top-off](#battery-top-off).
- **Precision**: each sensor gets a display precision from its unit and topic, such as 1 decimal for a voltage and 3 for a cell voltage or a cell imbalance.
- **Input boxes**: each number setting is an input box with a step from its unit, made finer by the decimals of its limits and its value, so 57.4 V can be set.
- **Time entities**: each time setting is a `time` entity, which takes any minute and shows a value that is not a quarter hour.
- **Reliable writes**: one write at a time per unit, with `verify`, so the unit reads each value back, and two retries of a 422. A write that still fails raises a repair issue.
- **Switches**: each setting with the options Enabled and Disabled is a switch.
- **Actions**: `solar_assistant.set_setting`, `solar_assistant.start_topoff` and `solar_assistant.stop_topoff`.
- **Diagnostics**: the download of the entry lists every topic with its definition, its options and its value, with the credentials redacted.
- **Battery SOC spread**: the largest pack SOC minus the smallest, and an alert above a set spread.
- **Data sensors**: one per inverter and battery, off when the device sent no changed value for a set time, because SolarAssistant repeats the last values while a cable is out.
- **Fewer recorder writes**: an optional minimum update interval per sensor. A value that crosses zero updates at once.
- **Discovery**: the unit shows on the Discovered card, and a known unit on a new IP gets its address updated.
- Fixes of upstream issues #21, #28, #31, #32, #34, #35 and of the options-flow bug of PR #27.

To install it through HACS, add `https://github.com/ColColonCleaner/ha_solar_assistant` as a custom repository of the category **Integration**, in place of the upstream repository.

## Features

- **Live data**: PV power, battery state of charge, grid power, load power, voltage, current, temperature, and more, streamed in real time
  over WebSocket.
- **Writable settings**: max charge/discharge current, work mode schedules, stop/start capacities, and other inverter parameters exposed as
  `number`, `select`, and `switch` entities.
- **Energy Dashboard**: cumulative energy totals (grid in/out, battery in/out, PV, load) exposed as `kWh` / `total_increasing`
  sensors, ready to drop straight into the Home Assistant [Energy Dashboard](https://www.home-assistant.io/docs/energy/).
- **Local or cloud**: works directly on your LAN with your web password, or via the SolarAssistant cloud with an API key.
- **Multi-unit**: add one config entry per SolarAssistant unit.

## Requirements

- Home Assistant 2026.11 or later. The fork uses `entry.runtime_data`, `AddConfigEntryEntitiesCallback` and the zeroconf service info of current Home Assistant, and it was tested on 2026.11.1 only
- A [SolarAssistant unit](https://solar-assistant.io/shop) on your LAN, or a SolarAssistant cloud account
- SolarAssistant on the unit: 
    - **2026-06-12 or later** for core telemetry and settings
    - **2026-07-02 or later** to enable the Energy Dashboard and system diagnostics sensors, such as temperature and free storage

## Installation

### Via Add-on (recommended)

1. On your Home Assistant, Go to **Settings → Apps → Install App → ⋮ → Repositories → Add**.
2. Provide `https://github.com/Solar-Assistant/ha_addons` for the URL and click **Add**. 
3. Find **SolarAssistant** in the app store, install it, and click **Start**.

Home Assistant will restart automatically and the integration is ready to configure.

### Manual

1. Download the [latest release](https://github.com/Solar-Assistant/ha_solar_assistant/releases/latest) and unzip it.
2. Copy the `custom_components/solar_assistant` folder into `/config/custom_components/` on your Home Assistant instance. The easiest way to
   reach `/config` without a terminal is via the **Samba share** app (Settings → Apps → Samba share). The **Terminal & SSH** app also works.
3. Restart Home Assistant.

### Via HACS

1. Install [HACS](https://hacs.xyz) if you don't have it yet.
2. In HACS, open the **⋮** menu (top right) and select **Custom repositories**.
3. Add `https://github.com/Solar-Assistant/ha_solar_assistant` with category **Integration**.
4. Find **SolarAssistant** in HACS, click **Download**, then restart Home Assistant.

> **Note**: SolarAssistant is not on the HACS default list yet, so adding the custom repository URL above is required.

## Updating

### Via Add-on

New releases show up as an update on the **SolarAssistant** app.

1. Go to **Settings → Apps → SolarAssistant** and click **Update**.
2. When it finishes, click **Start**.

**Update** fetches the new add-on version and **Start** applies it - downloading the matching integration release and restarting Home
Assistant Core. If the update does not show yet, click **Check for updates** in the top-right of **Settings → Apps**.

### Manual

Repeat the manual install for the [latest release](https://github.com/Solar-Assistant/ha_solar_assistant/releases/latest): copy the new
`custom_components/solar_assistant` folder over the existing one, then restart Home Assistant.

## Configuration

After the installation, go to **Settings → Devices & Services → Add integration** and search for **SolarAssistant**.

### Local password

Use this if your SolarAssistant unit is on the same network as Home Assistant.

1. Choose **Local password** when prompted.
2. Enter the unit's hostname or IP address (try `solar-assistant.local` first) and your SolarAssistant web interface password.

### Cloud API key

Use this if you have a SolarAssistant cloud account, or if the unit is not directly reachable.

1. Generate an API key at [solar-assistant.io](https://solar-assistant.io) under your account settings. 
2. Choose **Cloud API key** and paste the key.
3. Select which site to add.

## Entities

Entity names and counts depend on your inverter model. A typical single-inverter setup includes:

| Domain          | Examples                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------ |
| `sensor`        | PV power, battery state of charge, grid power, load power, voltage, current, temperature, inverter mode      |
| `number`        | Max charge/discharge current, max charge/discharge power, stop charge/discharge capacity, voltage thresholds |
| `select`        | Work mode slot priority, work mode start/end time, export limiter source                                     |
| `switch`        | Work mode slot enabled                                                                                       |
| `binary_sensor` | Connection status                                                                                            |

### Battery top-off

A lithium battery bank that rarely reaches full drifts: the packs show different states of charge, and the BMS balances the cells only near full. The top-off charges the bank from the grid to full when it has not been full for a set number of days.

It is **off by default**. The entities sit on the device of the SolarAssistant unit:

| Entity | Default | Meaning |
| --- | --- | --- |
| Battery top-off (switch) | off | turns the feature on |
| Top-off interval | 7 d | days without a full charge before a top-off is due |
| Top-off charge current | 100 A | the grid charge current during a top-off |
| Top-off full charge | 100 % | the state of charge that every battery must report |
| Top-off hold time | 30 min | the time to stay at full, so the BMS can balance the cells |
| Top-off time limit | 8 h | a top-off that runs longer stops with an error |
| Top-off window start, end | 22:00, 06:00 | a due top-off starts only in this window, so it does not grid-charge while the sun could charge. Equal times mean any time |
| Start top-off, Stop top-off (buttons) | | run or stop a top-off now |
| Top-off state, Last full charge, Next top-off due (sensors) | | the state, with the saved settings and the last error as attributes |

"Full" means that every `battery_N/state_of_charge` reports at least the full charge setting, or `total/battery_state_of_charge` when the unit reports no packs. Each full charge, from the sun or from a top-off, moves the next due time.

A top-off saves the current values of `grid_charge`, `grid_charge_current`, `grid_charge_according_to`, `grid_charge_slot_1_start` and `grid_charge_slot_1_end` of `inverter_1`. It then sets slot 1 to the whole day, the current to the setting, the mode to the time option and the grid charge to enabled. After the hold, it writes the saved values back, the mode first. The saved values persist, so a restart of Home Assistant during a top-off still restores them. A notification reports each start, end and error. A failed top-off waits for the start button, or for the switch to go off and on.

Measured on an EG4 12000XP with SolarAssistant 2026-08-25: at 53% SOC a top-off drew +96.6 A within a minute, with slot 1 from 00:00. At 95% SOC, just after the batteries were full from the sun, a top-off drew no charge for 12 minutes. Owners report that a BMS refuses charge after a full charge until the SOC falls below about 95%, so a top-off right after a full charge waits for that.

These are the grid-charge settings that SolarAssistant exposes for Luxpower and EG4 inverters. The EG4 12000XP manual limits the AC charge start SOC to 90 % and the AC charge current to 100 A, so the top-off uses a time slot. A unit without these settings, or without a time option, gets an error and no write.

### Topic filtering

By default, the integration subscribes to a curated set of the most useful live metrics. To choose which entities appear, go to Settings →
Devices & Services → SolarAssistant → **Configure**.

### Integration

See the [Entity contract](docs/entity-contract.md) if you are building an integration on top of the SolarAssistant entities.

## Contributing

Pull requests are welcome. The integration is a standard HA custom component under `custom_components/solar_assistant/`.

The Python client is the external [`py-solar-assistant`](https://pypi.org/project/py-solar-assistant/) package, pinned in
`manifest.json` (`requirements`) and installed by Home Assistant at setup. It is not vendored - to depend on a new client release, bump the
pinned version in `manifest.json` once it is published to PyPI.

To test an unreleased client against the integration on a running Home Assistant box, use [`scripts/release_local.sh`](scripts/release_local.sh),
which builds [`py_solar_assistant`](https://github.com/Solar-Assistant/py_solar_assistant) from a sibling checkout and deploys it alongside
the integration.

## Releasing

Maintainers cut releases from a developer machine with `scripts/release.sh`. See [RELEASING.md](RELEASING.md) for the full process.

## License

Apache 2.0 - see [LICENSE](LICENSE).

This licence covers the Home Assistant integration in this repository only. The SolarAssistant platform, including the downloadable device
software and cloud infrastructure, is proprietary and distributed under separate terms. See [NOTICE](NOTICE) for the copyright and scope
statement.

