# Using GroBro in Home Assistant

GroBro creates its devices in Home Assistant through MQTT discovery, there is nothing to set up in Home Assistant besides the MQTT integration. This guide covers what you can do with them.

- [The GroBro device](#the-grobro-device)
- [Energy dashboard](#energy-dashboard)
- [Smart meter readings for NOAH and NEXA](#smart-meter-readings-for-noah-and-nexa)
- [Support bundle](#support-bundle)

## The GroBro device

Besides your Growatt devices GroBro adds a device called **GroBro**. Your Growatt devices are shown as connected through it.

| Entity | Meaning |
|---|---|
| Version | The running GroBro version |
| Device Broker | Whether GroBro is connected to the broker your devices send to |
| Devices | How many devices sent data since GroBro started. Its attributes list each device with its detected type, inverter serial, last message and decoding errors |
| Messages Received | Messages received from your devices since GroBro started |
| Decode Errors | Messages GroBro could not process. If this keeps growing, please open an issue with a support bundle |
| Started | When GroBro was started |
| Create Support Bundle | Writes a support bundle, see [below](#support-bundle) |
| Last Support Bundle | File name of the last support bundle |

If a device doesn't show up, check `Device Broker` and the `Devices` attributes first: when the device isn't listed there, it doesn't reach the broker and the problem is on the network or broker side (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)).

## Energy dashboard

Pick these sensors in **Settings → Dashboards → Energy**. Counters that reset at midnight (`... today`) work as well, Home Assistant handles the reset.

| Device | Solar production | Grid consumption | Return to grid | Battery charge | Battery discharge |
|---|---|---|---|---|---|
| NEO | `Total generate energy` | – | – | – | – |
| NOAH | `PV energy today` | – | – | – | – |
| NEXA | `PV energy total` | – | – | `Battery charge energy today` | `Battery discharge energy today` |
| SPF | `PV1 energy total`, `PV2 energy total` | `AC charge total` (grid charging only) | `Export to grid total` | `Battery charge energy total` | `Battery discharge total` |
| TL-XH2, MIN, MOD, MID, SPH, SPA | `PV energy total` | `Lifetime grid import` | `Lifetime grid export` | `Lifetime battery charged` | `Lifetime battery discharged` |

Balcony devices (NEO, NOAH, NEXA) don't measure the grid connection. Use the sensors of your smart meter for grid consumption and return to grid.

## Smart meter readings for NOAH and NEXA

> **Experimental.** This passes readings to the device the way the Growatt Cloud and a Growatt smart meter do. It was verified on a NEXA 2000, the NOAH support is based on the writes of the Growatt Cloud.

NOAH and NEXA have a **Smart Mode** (slot mode `Smart Mode`), in which they follow the power at your grid connection by themselves. Normally that needs a Growatt smart meter. With GroBro you can pass the readings of any meter you already have in Home Assistant instead, e.g. a Shelly, a Tibber Pulse or a P1 reader.

GroBro adds two entities for this:

- `Smart Meter Reading`: the power at your grid connection in W, **positive for import, negative for export**
- `Clear Smart Meter Reading`: withdraws the reading

The device uses a reading for about two minutes. An automation has to send the current value whenever it changes and at least every 30 seconds:

```yaml
alias: Pass grid power to the NEXA
mode: single
max_exceeded: silent
triggers:
  - trigger: state
    entity_id: sensor.grid_power # your meter, positive import, negative export, in W
  - trigger: time_pattern
    seconds: "/30"
conditions:
  - condition: template
    value_template: "{{ states('sensor.grid_power') | is_number }}"
actions:
  - action: number.set_value
    target:
      entity_id: number.growatt_0hvrxxxxxxxxxxxx_smart_meter_reading
    data:
      value: "{{ states('sensor.grid_power') | float | round(0) }}"
```

Then set the mode of your slot (e.g. `Slot 1 Mode`) to `Smart Mode`. The device only accepts `Smart Mode` while it has a reading and silently keeps `Load First` otherwise, so start the automation first.

Good to know:

- **Only send what your meter measures at the grid connection.** The device corrects its output by each reading, it doesn't treat it as a target. A fixed or made up value makes it ramp its output up or down until it hits a limit, including discharging the battery for a load that isn't there.
- The device follows a new reading within a few seconds, but it may take up to three minutes after switching to `Smart Mode` before it starts. Sending the same value again only keeps the reading alive, it has no other effect.
- In `Smart Mode` the slot power is ignored.
- If the readings stop, the device keeps its output for about two minutes and then falls back to `Default Out Power`, not to zero. `Smart Meter Connected` shows whether the device currently has a reading.
- To stop, switch the slot back to `Load First` and press `Clear Smart Meter Reading`.
- If you only want to set a fixed output, use `Load First` and the slot power instead, see [Zero export](CONFIGURATION.md#2-recommended-zero-export-setup-via-home-assistant).

## Support bundle

Press **Create Support Bundle** on the GroBro device to write a file with everything needed to analyze a problem with a device:

- the GroBro version and your options without hosts, passwords and serials
- per device: the detected type, the last decoded values and settings and the last raw register messages

Serials of your devices, inverters and batteries are masked (e.g. `QMN0XXXXXXXXX1`), messages with network settings (Wi-Fi, IP addresses, broker) are not included at all. The file is written to the `support` folder of the GroBro config folder:

- Add-on: `addon_configs/<id>_grobro/support` (reachable e.g. with the Samba or File editor add-on)
- Container: `support` in the volume mounted at `/config`

Please have a look at the file before you attach it to an issue.
