# Authenticated Docker Compose example (Linux)

This example runs a dedicated TLS-enabled source broker and GroBro alongside an existing Home Assistant MQTT broker. It does not replace or restart the existing broker. Host networking is used for GroBro so the target can be reached on the Linux host's loopback interface.

## Setup

1. Copy `.env.example` to `.env` and restrict its permissions (`chmod 600 .env`). Replace the example LAN address and hostname. Use an internal DNS record pointing your own certificate hostname to the LAN address. Keep the existing Growatt hostname unchanged.
2. Install `chain-full.pem`, `root.pem` and `privkey.pem` in `mosquitto/certs/` as described in the [certificate guide](../../CERTIFICATES.md). Use the actual matching trust chain. Do not commit keys or password files.
3. Create the source broker password file interactively. Replace `DEVICE_SERIAL` with the actual MQTT username described in the [configuration guide](../../CONFIGURATION.md). Repeat without `-c` for each additional device. The device password is documented there. Use a separate strong password for the GroBro account.

```sh
mkdir -p mosquitto/certs mosquitto/data
docker run --rm -it --user 0:0 \
  -v "$PWD/mosquitto/config:/work" eclipse-mosquitto:2 \
  mosquitto_passwd -c /work/passwd DEVICE_SERIAL
docker run --rm -it --user 0:0 \
  -v "$PWD/mosquitto/config:/work" eclipse-mosquitto:2 \
  mosquitto_passwd /work/passwd grobro
```

Ensure the container's Mosquitto runtime user can read the certificate and password files and write the data directory. Check its UID/GID for the selected image (typically 1883:1883). Restrict private keys and password hashes to the necessary users; do not make them world-readable. `-c` overwrites an existing password file, so use it only on initial setup.

4. Create a separate `grobro` account on the existing HA broker using its normal administrative process. Enter the two passwords in `.env` (quote values as required by Compose). If the target broker uses TLS, set `TARGET_MQTT_TLS=true` and use its appropriate hostname/port. GroBro currently enables TLS without peer certificate validation, as documented in the configuration guide; keep this traffic on a trusted network.
5. Before starting, restrict source port 7006 to the intended devices and bridge on your network using firewall rules appropriate for Docker-published ports (including IPv6 where applicable). Do not forward the port from the internet. This example authenticates users but does not implement per-topic ACLs: apply and test ACLs appropriate to your device topics and GroBro's required subscriptions/publishes before using an untrusted network.

```sh
docker compose config --quiet
docker compose up -d
docker compose logs --tail=50
```

Check that both brokers accept GroBro's connections, then follow the upstream device configuration guide for one device at a time. Record its original server settings before changing them. Cloud forwarding is disabled in this example; the manufacturer app may stop receiving new data. No inverter settings are changed by Compose. Verify data and discovery before adding other devices or enabling control automations.

## Validation scope

The underlying two-broker layout, ARM64 container startup, local DNS, TLS 1.2/1.3 handshakes and separate MQTT authentication were verified in a Raspberry Pi 5 installation. This generalized Compose example still needs review and end-to-end testing with actual inverter telemetry and Home Assistant discovery. Pin reviewed image versions or digests for a long-lived installation.
