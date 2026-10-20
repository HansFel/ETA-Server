# ESP32-Temperaturen im ETA-Heiz-Kontroller

Am 19.10.2026 auf dem NAS aktiviert: Image `eta-heiz-kontroller:mqtt-sensors-20261019`.

## Bedienung und Rechte

- Übersicht: eigener Abschnitt **ESP32-Temperaturen** mit aktuellen Werten und Zeitpunkten.
- **Verlauf anzeigen** wählt den Sensor im vorhandenen Diagramm. Mehrere Sensoren lassen sich unter **Messgrößen** gemeinsam auswählen. Zeiträume: Stunde, Tag, Woche, Monat oder frei; CSV-Export verwendet dieselbe Bereichsprüfung.
- Administration → MQTT-Schnittstelle → **ESP32-Temperatursensoren**: pro aktivem MQTT-Gerät einen Bereich auswählen. Ohne Zuordnung keine neue Aufzeichnung; vorhandene Punkte sind dann nur Administratoren sichtbar.
- Das Gerät `eta-io-189837` ist ausdrücklich **Wiesenegger** zugeordnet. Personen mit Sehen/Schalten für Wiesenegger sehen seine vier Sensoren. Das Gerätekonto selbst behält seine bisherigen Rechte.

## Datenfluss

Die bestehende MQTT-Brücke abonniert `etaio/+/sensors`. Der Broker erlaubt `eta-app` dafür ausschließlich Subscribe. Der vorhandene ESP32 sendet alle fünf Sekunden eine JSON-Liste mit `id`, `name`, `topic` und `temperature`.

Die App prüft aktives MQTT-Gerätekonto, Administratorzuordnung, Topic-Eigentümer, Sensor-ID und Temperaturbereich −55 bis 125 °C. Sensor-ID und Gerät bilden die dauerhafte Identität; Namen und Untertopics können sich ändern. Null/ungültig wird als Messlücke gespeichert. Retained-Nachrichten beim Abonnieren werden ignoriert, da der ESP32 keinen verlässlichen Messzeitpunkt mitsendet. Neue Live-Nachrichten erhalten den Empfangszeitpunkt des Servers, intern UTC; Anzeige Europe/Vienna. Nach 90 Sekunden ohne Empfang gilt der Livewert bei der aktuellen 30-s-Konfiguration als veraltet.

Vorhandene Tabellen `Point`, `Sample` und `SampleArchive`, keine Schemaänderung. MQTT-Punkte sind ausschließlich lesend und haben `mqtt:`-URIs; der ETA-REST-Collector überspringt diese Punkte. Neue Samples im konfigurierten `POLL_SECONDS`-Intervall (NAS: 30 s), Gültigkeitswechsel sofort. Bestehende Archivierung und Datensicherung erfassen die Sensorhistorie mit. Die Historie beginnt mit Aktivierung; keine rückwirkenden Messwerte werden erzeugt.

Sensorwerte werden nicht auf den allgemein lesbaren MQTT-Topics `eta/state/#` veröffentlicht. Gerätespezifische Zustandstopics verwenden die bestehenden Bereichsrechte. HTTP-Livewerte, Historie und CSV prüfen ebenfalls die Bereichsrechte. Leere Diagrammraster zwischen regelmäßigen Messungen unterbrechen die Kurve nicht mehr; tatsächliche ungültige Werte und Messausfälle bleiben Unterbrechungen.

## Verifikation

- 74 isolierte Django-Tests erfolgreich, darunter sechs neue Tests für Logging, Intervall, Identität, Retained-/Fremdgeräteabwehr, Gültigkeitswechsel, Bereichsrechte, Zuordnung und Trennung vom ETA-REST-Collector.
- JavaScript-Syntaxprüfung erfolgreich. Headless-Edge mit synthetischen Messwerten auf 1440 und 390 Pixeln: Temperaturkarte, Auswahl der Verlaufskurve, keine JavaScript-Fehler und kein horizontaler Überlauf. Bilder unter `artifacts/mqtt-sensors/` sind Testdarstellungen.
- Vor Produktionsänderung: vorhandenes Backupverfahren `snapshot('pre-update')` und tatsächlicher isolierter `restore_test` erfolgreich.
- NAS: vier reale Sensoren empfangen und gespeichert: Vorlauf FW, Haus_Retour, Retour FW, Haus_Vorlauf. Web-Health HTTP 200, Workerprüfung erfolgreich.

## NAS-Betrieb und Rollback

### Temperaturdifferenzen ausschließlich im Diagramm (19.10.2026)

Aktueller Stand: `eta-heiz-kontroller:mqtt-graph-only-20261019`. Die App speichert ausschließlich gemessene Temperaturen. **Temperaturdifferenz FW (berechnet)** und **Temperaturdifferenz Haus (berechnet)** sind auswählbare Diagrammdefinitionen, keine aufgezeichneten Messgrößen. Ihre Point-Einträge enthalten nur Konfiguration und Einheit, keine aktuellen Differenzwerte. Keine Differenz-Samples, keine Differenz-Archivblöcke und keine Veröffentlichung berechneter Werte per MQTT. Keine Differenz-Livekarten; CSV-Export ist für gemessene Temperaturen vorgesehen.

Rechnung beim Abruf der Historie: jeweils Vorlauf minus Retour, Anzeige **°C** (Benutzerwunsch). Negative Werte bleiben negativ. Eingänge: `vorlauf-fw`/`retour-fw` und `haus-vorlauf`/`haus-retour` unter `etaio/<Gerät>/…/temperature`. Der gemeinsame Leser bezieht gemessene Samples und deren Archive ein. Gleichzeitige Eingänge werden gemeinsam verarbeitet; zeitversetzte Eingänge dürfen höchstens 1,5 Abfrageintervalle alt sein (bei 30 s also 45 s). Fehlende, ungültige oder zu alte Eingänge erzeugen Lücken. Alte gespeicherte Differenzwerte werden bei der Berechnung nicht gelesen.

Bereichsrechte gelten für Definition und beide Eingänge. Die Originalmesswerte ermöglichen die Berechnung auch für frühere Zeiträume. Bisherige Differenz-Samples und -Archivblöcke wurden nach einer geprüften Sicherung gezielt bereinigt. Geschützte Update-/Rollback-Dateien: `backups/mqtt-graph-only-20261019/`. Die vorherigen Images mit aufzeichnenden Differenzen würden erneut Differenz-Samples erzeugen und sind für den gewünschten Betrieb ungeeignet.

Tests prüfen Rechnung ohne Speicherung, negative Werte/Einheit, ungültige/veraltete Eingänge, gemeinsamen Archivleser und Bereichsrechte. Vor dem Rollout liefen alle 77 Tests isoliert erfolgreich.

Projekt `/volume1/docker/instanzen/eta`, Compose-Projekt `eta`. Für alle drei Dienste weiterhin `compose.yaml`, `compose.deployed.yaml` und `compose.mqtt.yaml` gemeinsam verwenden. Web/Worker/Init sind im Deployment-Override festgelegt; `.env` enthält die geprüfte Image-ID als `ETA_IMAGE` für die MQTT-Brücke. SMTP-Konfiguration bleibt erhalten. NAS-Quelldateien wurden mit aktualisiert.

Geschützte Konfigurations-/Quellsicherungen: `backups/mqtt-sensors-20261019/` (enthält auch geheime `env.before`, nicht weitergeben). Dort liegen `compose.deployed.before.yaml`, `emqx-acl.before.conf` sowie `before/` mit den vorherigen Quelldateien.

Vor Rückkehr zu einem älteren App-Image die MQTT-Brücke stoppen und die MQTT-Punkte deaktivieren (`Point.objects.filter(uri__startswith='mqtt:').update(enabled=False)`), damit der alte REST-Collector sie nicht abfragt. Danach vorherige Compose- und Env-Konfiguration wiederherstellen und Web/Worker/Brücke mit den drei Compose-Dateien starten. Vorhandene Sensorhistorie erhalten; keine alte Datenbank über aktuelle Messdaten zurückspielen. Änderungen an Produktivdaten auch beim Rollback erst nach geprüfter Sicherung.
