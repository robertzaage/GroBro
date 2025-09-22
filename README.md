# GroBro - Growatt MQTT Message Broker

GroBro is a bridge service that decodes encrypted MQTT packets from Growatt NEO, NOAH, NEXA, SPF (Shine WiFi-X) devices and republishes them in a format compatible with Home Assistant. 
It supports auto-discovery via MQTT and allows full integration of Growatt data into your smart home.

![GroBro Logo](https://raw.githubusercontent.com/robertzaage/GroBro/refs/heads/main/assets/grobro_logo.png)

Join us at [#grobro:matrix.org](https://matrix.to/#/#grobro:matrix.org)

---

## Features
- Decodes and maps encrypted register payloads from Growatt NEO/SPF-series inverters and NOAH/NEXA-series batteries
- Bridges inverter data from a dedicated MQTT source
- Proxies messages to the Growatt Cloud to keep the ShinePhone app functional (optional)
- Enables a local-only setup - keeping your device off the cloud
- Supports Home Assistant MQTT auto-discovery
- Containerized and configurable via environment variables

---

Example of a Growatt NEO 800M-X sending its data to Home Assistant:
 
![HA Screenshot](https://raw.githubusercontent.com/robertzaage/GroBro/refs/heads/main/assets/ha_device_screenshot.png)

## Setup Instructions

1. Configure your **Growatt NEO/SPF inverter** or **NOAH/NEXA battery** to send data to a custom MQTT broker
2. Configure a Mosquitto instance **with TLS**
3. Run **GroBro HA Bridge** Container

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
  ghcr.io/robertzaage/grobro:latest
```

### Installation as Add-On
1. Click the button below to add this repositiory on your Home Assistant instance

   [![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frobertzaage%2FGroBro)

2. Refresh your add-ons in your `Add-On Store` and search for `GroBro`
3. Click the `Install` button to install the add-on
4. Configure the `GroBro` add-on. Don't forget to set your register filter.
5. Start the `GroBro` add-on
6. Check the logs of the `GroBro` add-on to see it in action

## Hint
Growatt NEO, NOAH and NEXA devices rely on a TLS-enabled Mosquitto broker to send their packages. 
The full trust chain must be present, including the root certificate. [View the Certificates Guide](https://github.com/robertzaage/GroBro/blob/main/CERTIFICATES.md) for setup instructions.

## Contributions
Questions? Issues? PRs welcome!
