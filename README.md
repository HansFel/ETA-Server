# ETA-Server

Deutschsprachige Web-Anwendung zur Erfassung, Auswertung und berechtigungsgesteuerten Bedienung einer ETA-Heizung. Entwickelt an einer eHACK 50 mit Unterst?tzung von Claude Code und Codex. Unabh?ngiges Projekt, keine offizielle ETA-Software.

## Funktionen

- Serverseitige Messwerterfassung mit SQLite, Zeitstempel, Einheit und Qualit?tsstatus
- Live-?bersicht, historische Diagramme, Zeitraumauswahl und CSV-Export
- Leistung, Auslastung, Laufzeiten und Starts, soweit die Anlage passende Daten liefert
- Personenbezogene Lese- und Schaltrechte je Anlagenbereich
- Wochenzeitschaltuhr und freigegebene Schaltpunkte mit R?cklesen und Protokoll
- Automatische Sicherungen, Wiederherstellung und Messwertarchivierung
- Optionale MQTT-Br?cke und ESP32-Firmware f?r zus?tzliche Sensoren und IO

Datenpunkte und Steuerfunktionen sind anlagenspezifisch. Die vorhandenen URI-Zuordnungen vor Verwendung lesend an der eigenen Anlage pr?fen. Fehlende Werte werden nicht als null interpretiert. Die Auslastungsberechnung verwendet derzeit 50 kW als Bezug. Steuerung ist standardm??ig gesperrt und muss bewusst freigegeben werden.

## Installation mit Docker

Voraussetzungen: Docker mit Compose und eine vom Container erreichbare ETA-REST-Schnittstelle. Der Browser allein erfasst keine Messwerte; der Worker muss dauerhaft laufen.

```sh
git clone https://github.com/HansFel/ETA-Server.git
cd ETA-Server
cp .env.example .env
mkdir -p .secrets backups
python -c "import secrets,pathlib; p=pathlib.Path('.secrets'); (p/'django-key').write_text(secrets.token_urlsafe(64)); (p/'admin-password').write_text(secrets.token_urlsafe(24))"
```

In `.env` insbesondere `ETA_URL` (Adresse und Port), `POLL_SECONDS`, `INSTANCE_NAME`, `PUBLIC_URL` und `ALLOWED_HOSTS` einstellen. SMTP ist f?r Einladungen und Passwortwiederherstellung erforderlich. Die erzeugten Geheimnisse gesch?tzt aufbewahren; das Anfangspasswort steht in `.secrets/admin-password`. Keine vorhandenen Geheimnisdateien mit dem obigen Erzeugungsbefehl ?berschreiben.

Der Container l?uft als UID 10001. Unter Linux muss `backups` f?r diesen Benutzer schreibbar sein (z. B. `sudo chown 10001:10001 backups`). Die Dateien in `.secrets` m?ssen f?r den Container lesbar sein; Zugriff auf dem Host auf die n?tigen Personen beschr?nken.

```sh
docker compose up -d --build
```

Auf demselben Rechner unter http://localhost:8098 anmelden, Benutzer `admin` (?ber `BOOTSTRAP_USER` konfigurierbar). Der Port bindet standardm??ig nur an localhost. F?r andere Ger?te einen passend abgesicherten Reverse-Proxy konfigurieren. F?r einen Unterpfad wie `/eta` zus?tzlich `SCRIPT_NAME` setzen. Die ETA-Schnittstelle selbst nicht ins Internet freigeben.

`compose.yaml` enth?lt Init, Web und Erfassungsworker. MQTT ist optional: siehe [Broker-Einrichtung](nas-emqx/README.md), [MQTT-Steuerung](docs/mqtt-schalten.md) und `compose.mqtt.yaml`. Andere Compose-Dateien und Update-Anleitungen enthalten Beispiele aus der Entwicklung und m?ssen an die eigene Installation angepasst werden.

## ESP32

Siehe [Firmware-Dokumentation](firmware/eta-io/README.md). Vor dem ersten PlatformIO-Build:

```sh
python firmware/eta-io/scripts/provision.py
```

Dieser Befehl erzeugt individuelle Zugangsdaten in `firmware/eta-io/private/access.json` und den ignorierten Header `include/provisioning.h`. F?r MQTT mit TLS das eigene CA-Zertifikat in `firmware/eta-io/include/mqtt_ca.h` einsetzen; die ver?ffentlichte Datei enth?lt bewusst kein installationsspezifisches Zertifikat. Den eigenen Header nicht ver?ffentlichen. Lokale Zugangskarten und Firmware-Abbilder sind nicht enthalten.

F?r den optionalen Firmware-UI-Test wird `playwright` mit installiertem Microsoft Edge ben?tigt (`npm install --no-save playwright`, dann `node firmware/eta-io/tests/ui_test.cjs`).

## Tests und Grenzen

Django-Tests laufen isoliert ohne reale Heizungsbefehle:

```sh
docker compose run --rm --no-deps -e DATA_DIR=/tmp/eta-tests -e BACKUP_DIR=/tmp/eta-backups web python manage.py test heating --noinput
```

Sicherungen und Wiederherstellung vor dem produktiven Einsatz an einer isolierten Installation pr?fen. Lokale Testresultate sind keine Aussage ?ber andere Anlagen oder eine produktive Installation. Keine Datenbanken, Zugangsdaten, privaten Schl?ssel, realen Messwertaufnahmen oder lokalen Kontaktdateien sind Teil dieses Uploads.

## Lizenz

Copyright (c) 2026 Johann Felfer. Eigener Code und eigene Dokumentation stehen unter der [MIT-Lizenz](LICENSE), soweit einzelne Dateien keine abweichenden Hinweise tragen. Die Software wird ohne Gew?hrleistung bereitgestellt. Fremde Komponenten behalten ihre jeweiligen Lizenzen: [NOTICE.md](NOTICE.md). Siehe auch [CONTRIBUTING.md](CONTRIBUTING.md) und [SECURITY.md](SECURITY.md).
