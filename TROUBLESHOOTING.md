# MQTT and network troubleshooting

Use these checks before changing device settings. They apply to a Linux Docker deployment with separate source and Home Assistant target brokers. Adapt names, hostnames and ports to your installation.

## Device availability and DNS

- Identify the device by its serial/MAC in your router's client list and record its current server configuration. Some solar-powered devices may disappear after dark. Retry during adequate daylight.
- Ping confirms IP reachability only. An inverter initiating an outbound MQTT connection does not need to expose an inbound MQTT listener or web interface.
- Check the local broker hostname with `getent ahostsv4 mqtt.example.com`. If using a local DNS resolver, check that the device actually uses it. No matching DNS log entry does not prove there is no connection: cached answers, alternate DNS or fixed IPs are possible.
- On your own gateway, inspect connection tracking for the device's IP, if supported. A capture on a switched LAN host normally cannot see all other clients' unicast traffic. Avoid publishing packet captures, serials or sensitive identifiers.
- An established TCP connection to port 7006 is evidence of transport connectivity, not proof of MQTT authentication or successful decoding.

## TLS checks

```sh
openssl s_client -connect mqtt.example.com:7006 \
  -servername mqtt.example.com -verify_hostname mqtt.example.com \
  -verify_return_error -tls1_2 -brief </dev/null
```

Inspect the peer hostname, expiry and certificate chain. Repeat with `-tls1_3` if relevant. The minimum TLS setting on a broker can permit newer versions too. See [Certificates](CERTIFICATES.md). A successful OpenSSL handshake proves the test client's trust path only, not the embedded firmware's trust store or MQTT login. Do not bypass certificate checks to conceal a chain/hostname problem.

## Source versus target authentication

```sh
docker compose ps
docker logs --since 5m grobro
docker logs --since 5m grobro-mosquitto
docker logs --since 5m mosquitto
```

Check each broker separately. A source connection can succeed while the target reports `not authorised`. Confirm the configured username and the corresponding account in the correct password file, target TLS setting, port and host. GroBro connection attempt messages alone are not proof of successful authentication; check broker-side acceptance.

If `mosquitto_passwd` reports `Read-only file system` inside a running broker, inspect its mounts (`docker inspect --format '{{range .Mounts}}{{println .Source .Destination .RW}}{{end}}' mosquitto`). Keep the runtime config mount read-only. Back up the host-side password file and use an administrative tool or a temporary container with a writable mount to edit that file interactively. Never use `mosquitto_passwd -c` on a populated file. Restore appropriate ownership/permissions and reload the broker if supported. Check existing clients still authenticate. Do not print password hashes or Compose-expanded secrets into shared logs.

## Device connects but Mosquitto drops it

Some dataloggers (e.g. the ShineWiFi-X of MOD inverters) use an MQTT client that isn't fully spec conform. Mosquitto is strict about this and rejects the client registration, so the TLS handshake succeeds but no messages arrive. GroBro logs then show no data from this device, while the Mosquitto log shows the client disconnecting right after connecting.

In this case use [EMQX](https://github.com/emqx/emqx) as source broker on the TLS port, it is more forgiving and accepts these clients. Use its TLS listener, the plain listener rejects the connection with `invalid_proto_name`.

## Discovery and telemetry

Once both bridge logins work, verify a real device connects to the source broker, publishes messages, and GroBro decodes them. The GroBro device in Home Assistant shows the source broker connection and lists every device it received messages from, with its decoding errors. Then check MQTT discovery and sensor values in Home Assistant. The `DEVICE_TIMEOUT` option enables offline tracking when greater than zero; tune it to the reporting interval. Do not interpret unavailable power as measured zero unless the device actually reports it.

If a device is absent from the Growatt portal, check account/installer association and ask the seller or manufacturer for supported datalogger configuration access. Do not redirect the original cloud hostname to a broker with a different certificate or disable device certificate validation. Change one device at a time, retain a rollback path and verify whether the manufacturer's app still works.

## What to include in a support request

For problems with values or controls of a device, press `Create Support Bundle` on the GroBro device in Home Assistant and attach the file, see [Support bundle](HOME_ASSISTANT.md#support-bundle). It contains the last messages of your devices with masked serials. For connection problems the bundle doesn't help:

Provide GroBro version/image digest, device model and firmware (if known), relevant redacted logs, source/target connectivity outcomes, certificate issuer and chain, and whether the device was online. Remove passwords, tokens, private keys, public IPs, serial numbers and personal hostnames. Describe whether the failure occurs at transport, TLS, MQTT authentication, decoding or discovery.
