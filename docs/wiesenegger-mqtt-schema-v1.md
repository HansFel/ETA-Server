# Wiesenegger ESP32 MQTT-Schema V1

Stand: 2026-10-17
Status: Festgelegt für die Umsetzung von Netzweg 2a (ESP32 im WLAN der Familie, ausgehende Verbindung).

## 1. Ziel und Grenzen

- Der ESP32 sendet **Anforderung + Messwerte** über das öffentliche Netz.
- Nur die NAS-App schreibt in die ETA (ein zentraler Schreibpfad mit Audit und Rechteprüfung).
- Der ESP32 erhält **keine ETA-Zugangsdaten** und sendet keine ETA-Schreibbefehle.

## 2. Broker und Transport

- Transport: MQTT über TLS (Port 8883).
- QoS:
  - Telemetrie: QoS 1
  - Status/Availability: QoS 1
  - Kommandos: QoS 1
- Retain:
  - `.../status/availability`: `retain=true`
  - `.../telemetry`: `retain=false`
  - `.../command/*`: `retain=false`
- Last Will:
  - Topic `eta/wiesenegger/<device_id>/status/availability`
  - Payload `{"state":"offline","ts_utc":"...","reason":"lwt"}`

`<device_id>` ist eine feste, kurze Gerätekennung (z. B. `wg-esp32-01`).

## 3. Topic-Struktur

### 3.1 Vom ESP32 zur NAS-App

- `eta/wiesenegger/<device_id>/status/availability`
- `eta/wiesenegger/<device_id>/telemetry`
- `eta/wiesenegger/<device_id>/event`

### 3.2 Von der NAS-App zum ESP32

- `eta/wiesenegger/<device_id>/command/poll`

Der ESP32 darf auf `command/*` nur reagieren, wenn `target_device` seiner eigenen Kennung entspricht.

## 4. Payloads

Alle Zeitstempel sind UTC im ISO-8601-Format (`YYYY-MM-DDTHH:MM:SS.sssZ`).

## 4.1 Availability (`.../status/availability`)

```json
{
  "schema": 1,
  "state": "online",
  "ts_utc": "2026-10-17T17:10:00.000Z",
  "fw_version": "eta-io-1.4.0",
  "session_id": "8ef2db68-8b28-4b3d-9c4f-4a5c3f6cc8cd"
}
```

`state`: `online` oder `offline`.

## 4.2 Telemetrie (`.../telemetry`)

```json
{
  "schema": 1,
  "device_id": "wg-esp32-01",
  "seq": 1842,
  "ts_utc": "2026-10-17T17:10:05.000Z",
  "contact_demand": {
    "value": true,
    "quality": "ok"
  },
  "temperatures_c": {
    "vl_vor_wt": { "value": 43.2, "quality": "ok" },
    "rl_vor_wt": { "value": 36.4, "quality": "ok" },
    "vl_nach_wt": { "value": 41.8, "quality": "ok" },
    "rl_nach_wt": { "value": 34.7, "quality": "ok" }
  },
  "diag": {
    "rssi_dbm": -64,
    "uptime_s": 37820,
    "supply_v": 4.98
  }
}
```

`quality` pro Feld: `ok`, `stale`, `invalid`, `offline`.

## 4.3 Event (`.../event`)

Für Zustandswechsel (Kontaktwechsel, Sensorfehler, Neustart):

```json
{
  "schema": 1,
  "device_id": "wg-esp32-01",
  "seq": 1843,
  "ts_utc": "2026-10-17T17:10:07.110Z",
  "event": "contact_changed",
  "detail": {
    "contact_demand": false
  }
}
```

## 4.4 Polling-Kommando der NAS-App (`.../command/poll`)

```json
{
  "schema": 1,
  "command_id": "31f6d1a9-b17a-48aa-95c2-8b0885af4d49",
  "target_device": "wg-esp32-01",
  "poll_seconds": 15,
  "sent_at_utc": "2026-10-17T17:11:00.000Z"
}
```

Damit kann das Abfrageintervall zentral angepasst werden. Ein Kommunikations- oder Schaltkanal zur ETA wird damit nicht eröffnet.

## 5. Serverseitige Auswertung (verbindlich)

- Wärmeanforderung gilt nur bei frischen Daten:
  - `contact_demand.quality == "ok"`
  - letzter Datensatz nicht älter als 90 s
- Bei älteren Daten: Zustand `stale` anzeigen, Anforderung als ungültig behandeln.
- Keine fehlenden Werte auf 0 setzen.
- Jede Entscheidung protokollieren: Eingang, Bewertungsgrund, ETA-Befehl (falls gesendet), ETA-Rücklesen.

## 6. Eingeschränkter Web-App-Zugriff Familie Wiesenegger

Für Personen mit Bereich `Wiesenegger = Schalten` gilt der freigegebene Bedienumfang:

- `Ein/Aus` des Wiesenegger-Heizkreises.
- `Wochenschaltuhr` für den Wiesenegger-Heizkreis.
- Aktivierung über Wiesenegger-Steuermodus:
  - `permanent` oder
  - `esp_release` (nur mit aktiver, frischer ESP32-Freigabe).

Nicht freigegeben:

- Kessel, HK Haus, Puffer, Warmwasser, Werkstatt.
- Heizkurven, sofern dafür keine eigene Freigabe erteilt wurde.
- Backup, Personenverwaltung, globale Administrationsfunktionen.

Die Einschränkung wird serverseitig geprüft; ein direkter POST darf keine zusätzlichen Punkte schalten.

## 7. Wochenschaltuhr: Datenvertrag zur App

Die App speichert den Plan intern in Europe/Vienna; bei ETA-Schreibvorgängen werden UTC/Zeitzonenregeln korrekt umgesetzt.

```json
{
  "schema": 1,
  "timezone": "Europe/Vienna",
  "week": {
    "mon": [{"start": "05:30", "end": "08:30"}, {"start": "16:00", "end": "21:30"}],
    "tue": [{"start": "05:30", "end": "08:30"}, {"start": "16:00", "end": "21:30"}],
    "wed": [{"start": "05:30", "end": "08:30"}, {"start": "16:00", "end": "21:30"}],
    "thu": [{"start": "05:30", "end": "08:30"}, {"start": "16:00", "end": "21:30"}],
    "fri": [{"start": "05:30", "end": "08:30"}, {"start": "16:00", "end": "22:30"}],
    "sat": [{"start": "06:30", "end": "10:00"}, {"start": "16:00", "end": "22:30"}],
    "sun": [{"start": "06:30", "end": "10:00"}, {"start": "16:00", "end": "21:30"}]
  }
}
```

Regeln:

- max. 3 Zeitfenster pro Tag (ETA-Grenze im Katalog prüfen).
- `start < end`, keine überlappenden Fenster.
- Leere Liste bedeutet an diesem Tag kein Heizfenster.

## 8. Offener Implementierungspunkt vor Aktivierung

Die genauen ETA-Unterpunkte der Wochenschaltuhr für `/121/10103/12113/...` müssen vor der Aktivierung über `varinfo` verifiziert werden (Schreibbarkeit, Wertebereiche, Semantik je Wochentag/Zeitfenster). Ohne diese Verifikation keine Schreibfreigabe.
