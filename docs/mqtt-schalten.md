# Schalten über MQTT (Schnittstelle V1)

Stand 2026-10-17. Ergänzt `docs/wiesenegger-mqtt-schema-v1.md`: Geräte und Aufgaben (ESP32, Node-RED, Skripte)
dürfen Schaltpunkte schalten, aber **nie direkt in die ETA schreiben**. Die App-Brücke (`manage.py mqtt_bridge`)
nimmt Befehle an und schreibt mit denselben Prüfungen wie die Weboberfläche.

**Topic und Payload je Punkt:** in der App unter *Verwaltung → MQTT-Schnittstelle* (mit echtem Gerätenamen,
als Markdown herunterladbar) und in `docs/mqtt-punkte.md` (`python manage.py mqtt_doc [--device <name>]`).

## Broker und Zugang

- Broker: EMQX 6.3.2 auf der NAS, `nas-emqx/` (NAS-Ordner `/volume1/docker/instanzen/emqx`, Docker-Netz `mqtt`,
  Anleitung `nas-emqx/README.md`). Heimnetz 1883 mit Passwort, von außen nur **TLS 8883** (Portfreigabe).
  Keine anonymen Clients. Dashboard 18083 nur im Heimnetz.
- Serverzertifikat von eigener CA (`nas-emqx/certs.sh`). Clients prüfen den Server mit `certs/ca.crt`.
- Je Gerät ein EMQX-Konto (Dashboard, **nie Superuser**) **und** in der App ein Konto mit gleichem Namen,
  Häkchen „MQTT-Gerät“ und Rechten je Bereich (wie bei Personen, inkl. Wiesenegger-Profil).
- Feste Regeln in `nas-emqx/etc/acl.conf`: jedes Konto nur `eta/dev/<eigener-name>/...`, sonst ist `eta/` gesperrt.
  Deshalb erkennt die App das Gerät am Topic. Regeln für eigene Aufgaben kommen ins Dashboard.
- Jeder Schaltpunkt muss vom Administrator für MQTT freigegeben sein (Häkchen „Über MQTT schaltbar“).
  Voreinstellung: freigegeben, außer bei Punkten mit Rückfrage im Web (Kessel ein/aus, Heizkurven, Puffer min., WW-Priorität).

## Topics

| Richtung | Topic | QoS | Retain |
|---|---|---|---|
| Gerät → App | `eta/dev/<geraet>/cmd/<punkt>/set` | 1 | **nein** (gespeicherte Befehle werden abgewiesen) |
| App → Gerät | `eta/dev/<geraet>/cmd/<punkt>/result` | 1 | nein |
| App → Gerät | `eta/dev/<geraet>/state/<punkt>` | 1 | ja (alle sichtbaren Punkte, auch Messwerte; bei Rechteentzug gelöscht) |
| App → alle | `eta/state/<punkt>` | 0/1 | ja: **jeder** Datenpunkt (Messwerte und Einstellungen), lesbar für jedes Konto (`MQTT_PUBLISH_ALL=1`) |
| App → alle | `eta/status` (Abfragezustand) und `eta/errors` (ETA-Störungen; `items` = null, wenn unbekannt) | 1 | ja |
| Gerät | `eta/dev/<geraet>/status/...` (z. B. Availability/LWT) | 1 | nach Bedarf |
| App | `eta/app/availability` (`online`/`offline`) | 1 | ja (alle Konten dürfen lesen) |

`<punkt>` ist der Schlüssel aus `heating/management/commands/bootstrap.py` (z. B. `wiese-switch`, `wiese-time-mon-1`).

### Befehl (`.../set`)

Einfach (z. B. für Aufgaben): Payload ist nur der Wert, etwa `Ein`, `OFF`, `17.5` oder `06:00 - 09:30`.

Mit Wiederholungsschutz:

```json
{"command_id": "31f6d1a9-b17a-48aa-95c2-8b0885af4d49", "value": "Ein", "ts_utc": "2026-10-17T18:30:00Z"}
```

- `command_id`: UUID, je Befehl neu. Gleiche ID = Wiederholung, wird nicht erneut geschaltet.
- `ts_utc` optional; wenn vorhanden, wird ein Befehl älter als `MQTT_COMMAND_MAX_AGE` (120 s) abgewiesen.
- `value` je Punkttyp:
  - Auswahl: Klartext der ETA (z. B. `Ein`, `Aus`, `Nur WW`) oder Rohwert (`1803`); bei Ein/Aus-Punkten auch `ON`/`OFF`/`1`/`0`.
  - Zahl: in Anzeigeeinheit mit Dezimalpunkt (z. B. `17.5`), Bereich und Schrittweite laut ETA.
  - Zeitfenster: `HH:MM - HH:MM` im Viertelstundenraster (`24:00` = Mitternacht, `00:00 - 00:00` = kein Fenster).

### Ergebnis (`.../result`)

```json
{"schema": 1, "command_id": "...", "point": "wiese-switch", "status": "confirmed", "result": "Rückgelesen: Ein ", "raw": "1803", "text": "Ein", "unit": "", "ts_utc": "..."}
```

`status`: `confirmed` (zurückgelesen wie gewünscht), `different` (Anlage meldet anderen Wert), `uncertain`
(Ergebnis unklar, **kein** Neuversuch, Anlage prüfen), `rejected` (nicht gesendet, Grund in `reason`).

### Zustand (`.../state/<punkt>`)

```json
{"schema": 1, "key": "wiese-switch", "label": "Wiesenegger ein / aus", "section": "Wiesenegger", "value": 1803.0, "text": "Ein", "raw": "1803", "unit": "", "quality": "ok", "at": "...", "control": true, "choices": ["Aus", "Ein"], "type": "TEXT"}
```

Bei `quality` `stale`/`offline` sind `value`/`text` leer; nicht als 0 behandeln.

## Prüfungen der App (gleich wie im Web)

Gerätekonto aktiv, in Gruppe `mqtt`, **kein** Administrator; Schaltrecht für den Bereich; Wiesenegger-Profil
(eingeschränkt: nur Ein/Aus und Zeitschaltuhr) und ESP32-Modus; globale Freigabe; Punkt für MQTT freigegeben;
Wertebereich laut `varinfo`; höchstens 30 Befehle je Gerät in 15 Minuten; Rücklesen, `Command` (Rolle `mqtt`)
und Audit; jede Abweisung wird protokolliert.
