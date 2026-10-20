# EMQX-Broker auf der NAS

EMQX 6.3.2 (Community License: kostenlos für einen einzelnen Knoten ohne kommerzielle Nutzung) in
`/volume1/docker/instanzen/emqx` (am PC: `Z:\emqx`). Er ist allgemein nutzbar: für eigene Aufgaben und für den
ETA-Heiz-Kontroller. Die ETA-App ist nur ein Client (`eta-app`) im Docker-Netz `mqtt`.

| Port | Zweck | Router |
|---|---|---|
| 1883 | Heimnetz (192.168.178.32), ohne TLS, mit Passwort | **nicht** freigeben |
| 8883 | TLS, mit Passwort | **freigegeben** |
| 18083 | Dashboard (Weboberfläche), nur Heimnetz | **nicht** freigeben |

## 1. Starten

Der Ordner ist bereits vorbereitet: Zertifikat für `mqtt.example.invalid` und `192.168.178.32`,
Dashboard-Passwort in `.env`, Startkonto `eta-app`. Auf der NAS (SSH als root):

```bash
cd /volume1/docker/instanzen/emqx
chown -R 1000:1000 certs secrets && chmod 600 secrets/* certs/server.key .env && chmod 700 ca
docker compose up -d
docker compose logs --tail 30 emqx
```

Im Log müssen `Listener ssl:default on 0.0.0.0:8883 started` und `Listener tcp:default on 0.0.0.0:1883 started`
stehen. Dashboard: <http://192.168.178.32:18083>, Benutzer `admin`, Passwort aus `.env`
(`EMQX_DASHBOARD__DEFAULT_PASSWORD`). Nach dem ersten Login ein neues Passwort setzen.

## 2. Konten und Regeln (im Dashboard)

- **Konten:** Zugriffskontrolle → Authentifizierung → *Password-Based / Built-in Database* → Benutzer.
  **Nie „Superuser“ anhaken**: Superuser umgehen alle Regeln.
- **Gerät, das ETA-Punkte schaltet** (z. B. ESP32 `wg-esp32-01`): nur das Konto anlegen. Die Regeln dafür
  stehen fest in `etc/acl.conf` (nur `eta/dev/<eigener-name>/...`). Zusätzlich in der ETA-App unter
  „Personen & Zugänge“ ein Konto mit demselben Namen, dem Häkchen „MQTT-Gerät“ und dem Recht „Schalten“.
- **Eigene Aufgaben** (Node-RED, Shelly …): Konto anlegen, dann unter Zugriffskontrolle → Autorisierung →
  *Built-in Database* → „Benutzer“ eigene Regeln vergeben, z. B. `haus/#` erlauben.
  `eta/` ist immer gesperrt, außer für die oben genannten Regeln.

## 3. Verbinden

- Heimnetz: `192.168.178.32:1883`, Benutzer/Passwort.
- Von außen: `mqtt.example.invalid:8883`, TLS, CA-Datei `certs/ca.crt` (öffentlich, darf auf Geräte).
  Genau diesen Hostnamen verwenden, sonst schlägt die Zertifikatsprüfung fehl.
- Zertifikat erneuern (gilt 825 Tage): `bash certs.sh mqtt.example.invalid 192.168.178.32 && docker restart emqx`.
  Die CA bleibt gleich, Geräte müssen nichts ändern.

## 4. ETA-App anbinden

Setzt das neue ETA-Image `eta-heiz-kontroller:nas-20261017` voraus (Update über `nas-deploy.sh`, siehe
`C:\dev\ETA-Heiz-Kontroller\AGENTS.md`). Danach auf der NAS:

```bash
cd /volume1/docker/instanzen/eta
cp /volume1/docker/instanzen/emqx/secrets/eta-app-password .secrets/mqtt-app-password
chown 10001:10001 .secrets/mqtt-app-password && chmod 600 .secrets/mqtt-app-password
cp .env .env.vor-mqtt
grep -q '^MQTT_HOST=' .env || printf '\nMQTT_HOST=emqx\nMQTT_PORT=1883\nMQTT_USER=eta-app\nMQTT_PASSWORD_FILE=/run/secrets/mqtt-app-password\n' >> .env
export ETA_IMAGE=$(docker image inspect -f '{{.Id}}' eta-heiz-kontroller:nas-20261017)
docker compose -p eta -f compose.yaml -f compose.deployed.yaml -f compose.mqtt.yaml up -d mqtt-bridge
docker compose -p eta -f compose.yaml -f compose.deployed.yaml -f compose.mqtt.yaml logs --tail 20 mqtt-bridge
```

`compose.mqtt.yaml` vorher vom PC (`C:\dev\ETA-Heiz-Kontroller\compose.mqtt.yaml`) nach `Z:\eta\` kopieren.
In der ETA-App unter Verwaltung → MQTT-Schnittstelle muss „Brücke mit Broker verbunden“ stehen. Dort stehen für
jeden Schaltpunkt Topic und Payload.

## Sicherung und Rückbau

Sichern: `etc/`, `certs/`, `ca/` (geheim), `secrets/`, `.env`. Konten und Dashboard-Regeln liegen im
Docker-Volume `emqx_emqx-data`; im Dashboard unter System → Datensicherung exportierbar.
Rückbau: `docker compose down` (Volumes bleiben erhalten).

## Lokaler Test (PC)

`bash nas-emqx/init.sh localhost`, dann `cp nas-emqx/secrets/eta-app-password .secrets/mqtt-app-password` und
`docker compose -p eta-rechte -f compose.rechte.yaml -f compose.mqtt-rechte.yaml up -d --no-deps web mqtt mqtt-bridge`.
