# GroBro - Growatt MQTT Message Broker

GroBro is a bridge service that decodes encrypted MQTT packets from Growatt inverters and republishes them in a format compatible with Home Assistant. 
It supports auto-discovery via MQTT and allows full integration of Growatt data into your smart home.

![GroBro Logos](https://raw.githubusercontent.com/robertzaage/GroBro/refs/heads/main/assets/grobro_logo.png)

---

## Features
- Decodes and maps encrypted register payloads from Growatt NEO-series inverters and NOAH-series batteries
- Bridges inverter data from a dedicated MQTT source
- Proxies messages to the Growatt Cloud to keep the ShinePhone app functional (optional)
- Enables a local-only setup - keeping your device off the cloud
- Supports Home Assistant MQTT auto-discovery
- Containerized and configurable via environment variables

---

## Setup Instructions

1. Configure your **Growatt NEO inverter** or **NOAH battery** to send data to a custom MQTT broker
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

## Installation as Add-On:


1. Click the Add Add-ON Repository button below to add this repositiory on your Home
   Assistant instance.

   [![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frobertzaage%2FGroBro)

1. Refresh your add-ons in your Add-On Store and search for GroBro
1. Click the "Install" button to install the add-on.
1. configure the "Grobro" add-on.
1. Start the "Grobro" add-on.
1. Check the logs of the "Grobro" add-on to see it in action.

## Hint
Growatt NEO and NOAH devices rely on a TLS-enabled Mosquitto broker to send their packages. 
The full trust chain must be present, including the root certificate. [View the Certificates Guide](https://github.com/robertzaage/GroBro/blob/main/CERTIFICATES.md) for setup instructions.

## Contributions
Questions? Issues? PRs welcome!

