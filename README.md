# GroBro - Growatt MQTT Message Broker

[![CI](https://github.com/robertzaage/GroBro/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/robertzaage/GroBro/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/robertzaage/GroBro/branch/main/graph/badge.svg)](https://codecov.io/gh/robertzaage/GroBro)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue?logo=python)](https://github.com/robertzaage/GroBro)
[![GitHub Release](https://img.shields.io/github/v/release/robertzaage/GroBro?logo=github)](https://github.com/robertzaage/GroBro/releases)
[![License](https://img.shields.io/github/license/robertzaage/GroBro)](LICENSE)

GroBro is a bridge service that decodes the encrypted MQTT packets of Growatt inverters, batteries and dataloggers and republishes them in a format compatible with Home Assistant.
It works local-only or alongside the Growatt Cloud.

![GroBro Logo](https://raw.githubusercontent.com/robertzaage/GroBro/refs/heads/main/assets/grobro_logo.png)

Join us at [#grobro:matrix.org](https://matrix.to/#/#grobro:matrix.org)

---

## Supported Devices

| Device | Type | Serial prefix |
|---|---|---|
| NEO (e.g. NEO 800M-X, NEO 2000) | Balcony inverter | `QMN` |
| NOAH 2000 | Balcony battery | `0PVP` |
| NEXA 2000 | Balcony battery | `0HVR` |
| SPF (Shine WiFi-X) | Off-grid inverter | `HAQ` |
| TL-XH2 | Hybrid inverter | `ZGQ` |
| ShineWiFi-X (e.g. MOD series) | Datalogger, the inverter is identified by its serial | `VWQ` |
| ShineWeLink-X2 | Datalogger for LoRa inverters like the NEO 1000M-X | `RAQ` / `PTQ` |

Devices behind other dataloggers are identified by the serial of their inverter. This experimental support covers MIN/MIC TL-X, MIN TL-XH, MOD/MID TL3-XH, MOD TL3-HU, MAX TL3, WIT and the SPH/SPA storage inverters. Please open an issue with a message dump if your device shows wrong values.

## Features
- Local-only setup, your devices don't need the Growatt Cloud
- Optional forwarding to the Growatt Cloud, so the ShinePhone app keeps working
- Home Assistant MQTT auto-discovery, sensors are split into measurements, diagnostics and configuration
- Control your devices from Home Assistant, e.g. output power, charge and discharge limits, time slots, export limit and the inverter power switch. Battery controls for TL-XH, MOD and SPH inverters are experimental
- Controls keep their last confirmed value after a Home Assistant restart
- Optionally keeps the last known values of disconnected batteries and restores them after a GroBro restart
- Combined `Status` sensor with the derating reason and fault code, e.g. `Normal – Grid voltage high`
- Devices come back right after a Home Assistant restart and are marked unavailable when GroBro goes offline
- Optional automatic clock sync of your devices
- Publishes smart meter readings (e.g. EcoTracker, Shelly) reported by NOAH and NEXA to MQTT
- Optional TLS certificate verification for the MQTT connections
- Runs as Home Assistant add-on or as standalone container, configurable via environment variables

---

Example of a Growatt NEO 800M-X sending its data to Home Assistant:

![HA Screenshot](https://raw.githubusercontent.com/robertzaage/GroBro/refs/heads/main/assets/ha_device_screenshot.png)

## Setup Instructions

1. Configure a Mosquitto instance **with TLS**
2. Configure your Growatt device to send its data to this Mosquitto instance
3. Run GroBro as Home Assistant add-on or container

[View the Configuration Guide](https://github.com/robertzaage/GroBro/blob/main/CONFIGURATION.md) for details.

### Minimal Example
Mosquitto TLS for Growatt to plain Mosquitto configured in Home Assistant

```bash
docker run --rm \
  -e SOURCE_MQTT_HOST=<source-mqtt-host> \
  -e SOURCE_MQTT_PORT=<source-mqtt-port> \
  -e SOURCE_MQTT_TLS=true \
  -e TARGET_MQTT_HOST=<target-mqtt-host> \
  -e TARGET_MQTT_PORT=<target-mqtt-port> \
  -v ./grobro-config:/config \
  ghcr.io/robertzaage/grobro:latest
```

### Installation as Add-On
1. Click the button below to add this repository to your Home Assistant instance

   [![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frobertzaage%2FGroBro)

2. Refresh your add-ons in your `Add-On Store` and search for `GroBro`
3. Click the `Install` button to install the add-on
4. Configure the `GroBro` add-on with your source and target MQTT brokers
5. Start the `GroBro` add-on
6. Check the logs of the `GroBro` add-on to see it in action

## Hint
Growatt NEO, NOAH and NEXA devices rely on a TLS-enabled Mosquitto broker to send their packages.
The full trust chain must be present, including the root certificate. [View the Certificates Guide](https://github.com/robertzaage/GroBro/blob/main/CERTIFICATES.md) for setup instructions.

For diagnostic steps, see [MQTT and network troubleshooting](TROUBLESHOOTING.md).

## Contributions
Questions? Issues? PRs welcome!
