# Configuration

### 1. Prerequisites

You have a valid Let’s Encrypt certificate.

You have the following files:
- `fullchain.pem` – contains server + intermediate certificates
- `privkey.pem` – your private key

These are usually located in `/etc/letsencrypt/live/<your-domain>/`

Our setup needs the full trust chain including the root. Head over to the [Certificates Guide](CERTIFICATES.md) for details:
```bash
curl -o root.pem https://letsencrypt.org/certs/isrgrootx2.pem
cat fullchain.pem root.pem > chain-full.pem
```

So now you have:
- `chain-full.pem` (cert chain including ISRG Root X2 certificate)
- `privkey.pem` (private key)

### 2. Mosquitto TLS Configuration

Create a Mosquitto config file:
```
# Listener on port 7006 for TLS
listener 7006

# Path to certs and key
certfile /mosquitto/certs/chain-full.pem
keyfile /mosquitto/certs/privkey.pem
cafile /mosquitto/certs/root.pem

# Allow anonymous connections
allow_anonymous true
```

### 3. Run the Mosquitto TLS Container

Make sure your volume mounts place the certs and config properly:
```
/mosquitto
├── config
│   └── mosquitto.conf
├── certs
│   ├── chain-full.pem
│   ├── privkey.pem
│   └── root.pem
```

Now run the Mosquitto container like this and check the logs for any errors:
```bash
docker run --detach \
  --name mosquitto-tls \
  --publish 7006:7006 \
  --volume ./mosquitto-tls/conf:/mosquitto/config \
  --volume ./mosquitto-tls/data:/mosquitto/data \
  --volume ./mosquitto-tls/certs:/mosquitto/certs \
  docker.io/library/eclipse-mosquitto:latest
```

### 4. Setup the Growatt Device

Open the ShinePhone app and tap on `Devices List`. Select the inverter you want to configure for your own MQTT server and tap `Configure`.
Make sure you are within Bluetooth range and the inverter is powered on. Configuration cannot be performed at night when the inverter is off.

![Step 1](assets/config_menu_1.png)

Next, tap `Advanced` and open the `Server settings` tab. Tap the lock icon and enter the password, which is based on the current date:

`growatt<YYYYMMDD>`

Then tap `Yes`.

![Step 2](assets/config_menu_2.png)

Once unlocked, the settings can be modified. For `Server domain name/IP`, choose `Manual` and enter the address of your Mosquitto instance configured for TLS. Do the same for the `Port` field.
Return to the main configuration screen and tap `Configure immediately`. You can ignore the final step when the app attempts to connect to the Growatt cloud. After that, you may close the app.

![Step 3](assets/config_menu_3.png)

Additionally: Block the device from accessing the internet after configuration to prevent it from reverting settings or syncing with the cloud.

### 5. Run the GroBro HA Bridge

This example demonstrates how to run the GroBro HA bridge with a dedicated TLS-secured Mosquitto instance for the Growatt device as the source, and a separate MQTT broker for Home Assistant as the target:
```bash
docker run --detach \
  --name grobro-bridge \
  --env SOURCE_MQTT_HOST=<source-mqtt-host> \
  --env SOURCE_MQTT_PORT=<source-mqtt-port> \
  --env SOURCE_MQTT_TLS=true \
  --env TARGET_MQTT_HOST=<target-mqtt-host> \
  --env TARGET_MQTT_PORT=<target-mqtt-port> \
  ghcr.io/robertzaage/grobro:latest
```

### Environment Variable Reference

| Variable             | Required | Description                                                                 |
|----------------------|----------|-----------------------------------------------------------------------------|
| `SOURCE_MQTT_HOST`   | ✅ Yes   | Hostname or IP of the source MQTT broker (for Growatt)                     |
| `SOURCE_MQTT_PORT`   | ✅ Yes   | Port number of the source MQTT broker                                      |
| `SOURCE_MQTT_TLS`    | ❌ No    | Set to `true` to enable TLS without certificate validation                 |
| `SOURCE_MQTT_USER`   | ❌ No    | Username for the source MQTT broker (if authentication is required)        |
| `SOURCE_MQTT_PASS`   | ❌ No    | Password for the source MQTT broker                                        |
| `TARGET_MQTT_HOST`   | ✅ Yes   | Hostname or IP of the target MQTT broker (for Home Assistant)              |
| `TARGET_MQTT_PORT`   | ✅ Yes   | Port number of the target MQTT broker                                      |
| `TARGET_MQTT_TLS`    | ❌ No    | Set to `true` to enable TLS without certificate validation                 |
| `TARGET_MQTT_USER`   | ❌ No    | Username for the target MQTT broker (if authentication is required)        |
| `TARGET_MQTT_PASS`   | ❌ No    | Password for the target MQTT broker                                        |
| `HA_BASE_TOPIC`      | ❌ No    | Base MQTT topic used for Home Assistant auto-discovery and sensor states   |
| `REGISTER_FILTER`    | ❌ No    | Comma-separated list of `serial:alias` pairs (e.g. `123456789:NOAH,987654321:NEO800`). Allows specifying which register set to apply per device. Defaults to inverter register map if not set. |
| `ACTIVATE_COMMUNICATION_GROWATT_SERVER` | ❌ No    | Set to True to redirect messages to and from the Growatt Server. Default is False |

