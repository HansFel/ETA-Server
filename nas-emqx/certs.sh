#!/usr/bin/env bash
# TLS-Zertifikate für EMQX (Port 8883): eigene CA + Serverzertifikat.
#   bash certs.sh <dns-name> [ip ...]     z. B. bash certs.sh mqtt.example.invalid 192.168.178.32
# Die CA wird nur beim ersten Mal erzeugt; Erneuern ändert nur das Serverzertifikat (Geräte behalten ca.crt).
# Danach: docker restart emqx
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash unter Windows: /CN=... nicht als Pfad umschreiben
cd "$(dirname "$0")"
host="${1:?DNS-Name angeben, z. B. mqtt.example.invalid}"; shift
san="DNS:$host,DNS:emqx,DNS:localhost,IP:127.0.0.1"
for ip in "$@"; do san="$san,IP:$ip"; done
mkdir -p certs ca
if [ ! -f ca/ca.key ]; then
  # Strenge Clients (z. B. Python 3.13, aktuelle TLS-Bibliotheken) verlangen keyUsage und Schlüssel-IDs.
  openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=NAS MQTT CA" -keyout ca/ca.key -out certs/ca.crt \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" -addext "subjectKeyIdentifier=hash" 2>/dev/null
fi
openssl req -newkey rsa:2048 -nodes -subj "/CN=$host" -keyout certs/server.key -out ca/server.csr 2>/dev/null
printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\nbasicConstraints=critical,CA:FALSE\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n' "$san" > ca/server.ext
openssl x509 -req -in ca/server.csr -CA certs/ca.crt -CAkey ca/ca.key -CAserial ca/ca.srl -CAcreateserial -days 825 -extfile ca/server.ext -out certs/server.crt 2>/dev/null
rm -f ca/server.csr ca/server.ext
chmod 700 ca; chmod 600 ca/ca.key
chmod 644 certs/ca.crt certs/server.crt certs/server.key   # EMQX läuft im Container als uid 1000
echo "Serverzertifikat (825 Tage) für: $san"
echo "CA für Geräte/Clients: certs/ca.crt (öffentlich). ca/ca.key geheim halten."
