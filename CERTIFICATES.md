# Certificates

## Assembling a Complete Certificate Chain for TLS Verification

Recent Growatt devices are very strict when it comes to verifying TLS connections.
These devices **require the full certificate chain**, from your server certificate all the way up to the trusted root certificate, in exactly the right order.

If the chain is incomplete or incorrect, the device will refuse the connection—even if your certificate is technically valid and works fine in browsers.

### The Problem

When using Let's Encrypt certificates, the issued cert alone isn’t enough.
Let’s Encrypt uses **intermediate CAs** and **root CAs** to sign your cert.
The firmware may not have the full chain pre-installed, so you need to provide it explicitly.

### Example Scenario

Let’s say you’ve generated a certificate for `mqtt.example.com` via Let’s Encrypt. You inspect the connection using `openssl`:

```bash
openssl s_client -showcerts -connect mqtt.example.com:7006
```

And you see this chain:
1. Your server cert (CN=mqtt.example.com), issued by `E6`
2. Intermediate CA: Let's Encrypt `E6`, issued by `ISRG Root X2`
3. Root CA: `ISRG Root X2`

### Required Chain

To satisfy Growatt clients, you must provide a **fullchain.pem** that includes:
1. Your certificate
2. Intermediate certificate (`E6`)
3. Root certificate (`ISRG Root X2`)

![ISRG Hierarchy](./assets/isrg-hierarchy.png)
*Let’s Encrypt Certificate Hierarchy as of June 2024, showing the relationship between root, intermediate, and subscriber certificates. Source: [letsencrypt.org/certificates](https://letsencrypt.org/certificates/)*

### Step-by-Step: Building a Correct `fullchain.pem`

1. **Get Your Certificate**
   This is your own domain certificate, usually provided by certbot as `cert.pem`.

2. **Download the Intermediate Certificate (E6)**
   ```bash
   wget https://letsencrypt.org/certs/2024/e6.pem
   ```

3. **Download the Root Certificate (ISRG Root X2)**
   ```bash
   wget https://letsencrypt.org/certs/isrg-root-x2.pem
   ```

4. **Create the Full Chain**
   Concatenate all three in the correct order:
   ```bash
   cat cert.pem e6.pem isrg-root-x2.pem > fullchain.pem
   ```

5. **Use the Full Chain**
   Your Growatt device will now be able to verify the entire trust chain when connecting over TLS.

### Tips

- Always verify the chain with `openssl s_client` after creating `fullchain.pem`.
- If you’re unsure about which intermediate/root to use, check the [Let’s Encrypt certificate list](https://letsencrypt.org/certificates/).
- If the device is using a different root, make sure to follow the corresponding chain.

For automated renewal on a Linux host, see [Certbot renewal and Mosquitto deployment](CERTBOT_RENEWAL.md).

## Supported Root Certificates

These root certificates are embedded in the firmware and are considered trusted by the device.

**Amazon**
- Amazon Root CA 1
- Amazon Root CA 2
- Amazon Root CA 3
- Amazon Root CA 4

**Atos**
- Atos TrustedRoot 2011

**Baltimore**
- Baltimore CyberTrust Root

**Buypass**
- Buypass Class 2 Root CA
- Buypass Class 3 Root CA

**CA Disig**
- CA Disig Root R2

**Certigna**
- Certigna Root CA

**CertSIGN**
- certSIGN ROOT CA
- certSIGN ROOT CA G2

**CFCA**
- CFCA EV ROOT

**DigiCert**
- DigiCert Global Root CA
- DigiCert Global Root G2
- DigiCert Global Root G3
- DigiCert Trusted Root G4
- DigiCert Assured ID Root CA
- DigiCert Assured ID Root G2
- DigiCert Assured ID Root G3
- DigiCert High Assurance EV Root CA

**D-TRUST**
- D-TRUST BR Root CA 1 2020
- D-TRUST EV Root CA 1 2020
- D-TRUST Root Class 3 CA 2 2009
- D-TRUST Root Class 3 CA 2 EV 2009

**emSign**
- emSign Root CA - C1
- emSign ECC Root CA - C3
- emSign Root CA - G1
- emSign ECC Root CA - G3

**Entrust**
- Entrust Root Certification Authority
- Entrust Root Certification Authority - G2
- Entrust Root Certification Authority - G4
- Entrust Root Certification Authority - EC1

**GDCA**
- GDCA TrustAUTH R5 ROOT

**GlobalSign**
- GlobalSign Root CA - R3
- GlobalSign Root CA - R6
- GlobalSign ECC Root CA - R4
- GlobalSign ECC Root CA - R5
- GlobalSign Root E46
- GlobalSign Root R46
- GlobalSign Root CA

**GoDaddy / Starfield**
- Go Daddy Root Certificate Authority - G2
- Starfield Root Certificate Authority - G2
- Starfield Services Root Certificate Authority - G2

**GTS (Google Trust Services)**
- GTS Root R1
- GTS Root R2
- GTS Root R3
- GTS Root R4

**HARICA**
- HARICA TLS ECC Root CA 2021
- HARICA TLS RSA Root CA 2021

**Hellenic Academic and Research Institutions**
- Hellenic Academic and Research Institutions Root CA 2011
- Hellenic Academic and Research Institutions Root CA 2015
- Hellenic Academic and Research Institutions ECC Root CA 2015

**HiPKI**
- HiPKI Root CA - G1

**Hongkong Post**
- Hongkong Post Root CA 1
- Hongkong Post Root CA 3

**IdenTrust**
- IdenTrust Commercial Root CA 1
- IdenTrust Public Sector Root CA 1

**ISRG (Let's Encrypt)**
- ISRG Root X1
- ISRG Root X2

**Microsec**
- Microsec e-Szigno Root CA 2009

**Microsoft**
- Microsoft ECC Root Certificate Authority 2017
- Microsoft RSA Root Certificate Authority 2017

**NAVER**
- NAVER Global Root Certification Authority

**OISTE WISeKey**
- OISTE WISeKey Global Root GB CA
- OISTE WISeKey Global Root GC CA

**QuoVadis**
- QuoVadis Root CA 1 G3
- QuoVadis Root CA 2 G3
- QuoVadis Root CA 3 G3
- QuoVadis Root CA 2
- QuoVadis Root CA 3

**Security Communication**
- Security Communication RootCA1
- Security Communication RootCA2

**SSL.com**
- SSL.com Root Certification Authority ECC
- SSL.com Root Certification Authority RSA
- SSL.com EV Root Certification Authority ECC
- SSL.com EV Root Certification Authority RSA R2

**SZAFIR**
- SZAFIR ROOT CA2

**TeliaSonera / Telia**
- TeliaSonera Root CA v1
- Telia Root CA v2

**TrustCor**
- TrustCor RootCert CA-1
- TrustCor RootCert CA-2

**T-TeleSec**
- T-TeleSec GlobalRoot Class 2
- T-TeleSec GlobalRoot Class 3

**TWCA**
- TWCA Global Root CA
- TWCA Root Certification Authority

**UCA**
- UCA Global G2 Root
- UCA Extended Validation Root

**vTrus**
- vTrus Root CA
- vTrus ECC Root CA

**Actalis**
- Actalis Authentication Root CA

**e-Szigno**
- e-Szigno Root CA 2017

**ANF**
- ANF Secure Server Root CA
