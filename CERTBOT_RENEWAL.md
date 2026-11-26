# Automated TLS certificate renewal (Certbot / Mosquitto)

This optional Linux example supplements the [certificate guide](CERTIFICATES.md). DNS-01 validation allows a private MQTT broker to use a publicly trusted certificate without exposing a web server or MQTT port to the internet. Use a domain you control and a DNS provider plugin supported by Certbot.

For Cloudflare, install the Certbot DNS plugin for your distribution and create a token restricted to DNS editing for the relevant zone. Save it in a root-owned file with mode 0600; never commit it. The credential file contains `dns_cloudflare_api_token = YOUR_TOKEN`.

```sh
sudo certbot certonly --dns-cloudflare \
  --dns-cloudflare-credentials /etc/letsencrypt/credentials/grobro.ini \
  --dns-cloudflare-propagation-seconds 60 \
  --key-type rsa --rsa-key-size 2048 \
  --preferred-chain 'ISRG Root X1' -d mqtt.example.com
sudo certbot renew --cert-name mqtt.example.com --dry-run
```

A preferred chain is a request, not a guarantee. Inspect the actual issued chain (including any cross-signed CA) and select a matching root trusted by the device. Do not blindly append X2 to an X1 chain or vice versa. For example, an X1-anchored chain with a cross-signed intermediate root was verified with OpenSSL; that does not prove that every device firmware accepts it.

```sh
sudo openssl crl2pkcs7 -nocrl \
  -certfile /etc/letsencrypt/live/mqtt.example.com/fullchain.pem \
  | openssl pkcs7 -print_certs -noout
```

## Deploy hook example

Adapt the paths, container name, runtime UID/GID and trusted root to your installation. Create the destination directory beforehand, mount the directory (not individual certificate files) into the container, and configure Mosquitto to use `chain-full.pem` and `privkey.pem`. Verify the key and chain before replacing files. The hook only handles its selected Certbot lineage.

Save as `/etc/letsencrypt/renewal-hooks/deploy/grobro-certificates.sh` owned by root, mode 0700:

```bash
#!/usr/bin/env bash
set -euo pipefail
umask 077
CERT=/etc/letsencrypt/live/mqtt.example.com
DEST=/srv/grobro/mosquitto/certs
CA=/etc/ssl/certs/ISRG_Root_X1.pem
CONTAINER=grobro-mosquitto
[[ ${RENEWED_LINEAGE:-} == "$CERT" ]] || exit 0
openssl verify -CAfile "$CA" -untrusted "$CERT/chain.pem" "$CERT/cert.pem"
openssl x509 -in "$CERT/cert.pem" -noout -checkhost mqtt.example.com
cmp <(openssl x509 -in "$CERT/cert.pem" -pubkey -noout) \
    <(openssl pkey -in "$CERT/privkey.pem" -pubout)
TMP=$(mktemp -d "$DEST/.renewal.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
cat "$CERT/fullchain.pem" "$CA" > "$TMP/chain-full.pem"
cp "$CERT/privkey.pem" "$TMP/privkey.pem"
cp "$CA" "$TMP/root.pem"
chown 1883:1883 "$TMP/"*.pem
chmod 600 "$TMP/privkey.pem"
chmod 644 "$TMP/chain-full.pem" "$TMP/root.pem"
for file in chain-full.pem privkey.pem root.pem; do
  mv "$TMP/$file" "$DEST/$file"
done
if [[ $(docker inspect -f '{{.State.Running}}' "$CONTAINER") == true ]]; then
  docker kill --signal=HUP "$CONTAINER"
fi
```

Back up the existing certificate files before first use. Test the hook against the live lineage and inspect broker logs, then check a new TLS connection. If the broker fails to reload, restore the previous matching certificate/key pair and investigate. The example assumes a stable root; if the CA changes, verification fails before replacing files and an administrator must review the chain.

```sh
sudo env RENEWED_LINEAGE=/etc/letsencrypt/live/mqtt.example.com \
  /etc/letsencrypt/renewal-hooks/deploy/grobro-certificates.sh
openssl s_client -connect mqtt.example.com:7006 \
  -servername mqtt.example.com -verify_hostname mqtt.example.com \
  -verify_return_error -tls1_2 -brief </dev/null
```

Check your distribution's renewal scheduler and monitor expiry and hook failures. A successful dry run validates renewal authorization but does not by itself prove a production certificate was deployed. The underlying DNS-01 issuance, renewal dry run, manual hook invocation and broker reload were observed on a Raspberry Pi 5 with Mosquitto 2.1.2. Acceptance of the renewed chain by the inverter remains unverified.
