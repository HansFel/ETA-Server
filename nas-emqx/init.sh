#!/usr/bin/env bash
# Einmalige Einrichtung des EMQX-Ordners: Geheimnisse und Zertifikate erzeugen (bestehende bleiben erhalten).
#   bash init.sh <dns-name> [ip ...]     z. B. bash init.sh mqtt.example.invalid 192.168.178.32
# Erzeugt:
#   .env                       Node-Cookie und erstes Dashboard-Passwort (Benutzer admin)
#   secrets/users.csv          Startkonto eta-app für die ETA-App (wird beim ersten Start importiert)
#   secrets/eta-app-password   dasselbe Passwort für die ETA-App (nach <ETA-Ordner>/.secrets/mqtt-app-password kopieren)
#   certs/, ca/                TLS über certs.sh
set -euo pipefail
cd "$(dirname "$0")"
newpass() { openssl rand -base64 30 | tr -d '/+=\n' | cut -c1-28; }
mkdir -p secrets
if [ ! -f .env ]; then
  printf 'EMQX_NODE_COOKIE=%s\nEMQX_DASHBOARD__DEFAULT_PASSWORD=%s\n' "$(openssl rand -hex 24)" "$(newpass)" > .env
  echo ".env angelegt (Dashboard-Passwort für admin steht darin; nach erstem Login im Dashboard ändern)."
fi
if [ ! -f secrets/users.csv ]; then
  pw="$(newpass)"
  printf 'user_id,password,is_superuser\neta-app,%s,false\n' "$pw" > secrets/users.csv
  printf '%s' "$pw" > secrets/eta-app-password
  echo "Startkonto eta-app angelegt."
fi
chmod 600 .env secrets/* 2>/dev/null || true
bash certs.sh "$@"
if [ "$(id -u)" = 0 ]; then
  # EMQX läuft im Container als uid 1000
  chown -R 1000:1000 certs secrets && chmod 600 secrets/* certs/server.key
fi
