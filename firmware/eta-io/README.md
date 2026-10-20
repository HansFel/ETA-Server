# ETA IO – ESP32

Eigenständige ESP32-Firmware für lokale digitale IO, ETA-Warmwasser-Vorrang,
DS18B20-Temperaturen, optionale I²C-Sensoren und Home Assistant. Die NAS-App
wird für den Betrieb nicht benötigt. Die Firmware legt keine ETA-Funktionsblöcke an.

## Einrichtung

1. Die private Zugangskarte befindet sich in `private/ZUGANG.md`. Sie wird lokal
   erzeugt und gehört weder in Git noch in ein öffentlich geteiltes Firmwarepaket.
2. Ohne gespeichertes WLAN startet ein passwortgeschütztes WLAN
   `eta-io-<Gerätekennung>-Setup` für fünf Minuten. Verbinden und
   `http://192.168.4.1` öffnen; dort das 2,4-GHz-WLAN auswählen und verbinden.
3. Danach die vom Router bzw. seriell gemeldete IP im Browser öffnen. Anmeldung
   mit `admin` und dem individuellen Web-Passwort der Zugangskarte.
4. Alle acht IO-Plätze und Sensorbusse sind zunächst deaktiviert. Erst nach
   Auswahl von GPIO, Funktion und Polarität speichern. Jede Speicherung schaltet
   die Ausgänge aus und startet das Gerät neu.
5. WLAN-Assistent erneut öffnen: Web-Schaltfläche oder USB-Kommando `wifi`.
   `status` liefert Diagnose ohne Passwörter, `password-reset` setzt den lokalen
   Web-/OTA-Zugang auf die private Zugangskarte zurück. Baudrate: 115200.

## IO und Warmwasser

Funktionen je Platz: deaktiviert, Digitaleingang, manueller Ausgang,
WW-Freigabe. Manuelle Ausgänge haben eine maximale Einschaltdauer von
1 bis 3600 Sekunden und starten immer ausgeschaltet. Neue EIN-Befehle starten
diese Frist erneut. WLAN-Verlust beendet eine manuelle Einschaltung.

Die WW-Freigabe liest ausschließlich diesen bestehenden ETA-Datenpunkt:

`http://<ETA-IP>:8080/user/var/121/10111/0/0/12080`

| Antwort | Logische Elektro-Freigabe |
|---|---|
| 1803 / WW Ein | AUS / gesperrt |
| 1802 / WW Aus und aktuell | EIN / PV darf anfordern |
| Unbekannter Wert, falsches XML, HTTP-Fehler, WLAN-Ausfall oder Frist abgelaufen | AUS / gesperrt |
| Neustart, WLAN-Einrichtung, Firmware-Update oder fehlerhafte gespeicherte Konfiguration | AUS / gesperrt |

Standardabfrage: alle 5 Sekunden, Gültigkeit höchstens 20 Sekunden. Ein erkannter
Abfragefehler sperrt sofort nach Rückkehr der Anfrage. Netzwerkaufrufe haben
Zeitlimits; es handelt sich um eine zyklische Steuerung, nicht um eine
echtzeitfähige oder zertifizierte Sicherheitseinrichtung. NAS und Home Assistant
sind nicht an der WW-Entscheidung beteiligt. MQTT darf die WW-Freigabe nicht
überschreiben. Der Status beschreibt den GPIO-Befehl, nicht einen zurückgemeldeten
Schützzustand. Ein separater Digitaleingang kann einen Hilfskontakt anzeigen.

Ausgangspins: 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33.
Zusätzliche reine Eingänge: 34, 35, 36, 39 (ohne interne Pull-Widerstände).
Boot-, Flash- und USB-UART-Pins sind ausgeschlossen. GPIO 16/17 sind bei manchen
PSRAM-Modulen intern belegt; die vorliegende Firmware ist für den identifizierten
klassischen ESP32 ohne PSRAM vorgesehen. Doppelte Pinbelegungen werden abgewiesen.

Vorschlag für die spätere Hardware: WW-Freigaberelais auf GPIO 27,
DS18B20 auf GPIO 26, I²C auf SDA 21 / SCL 22. Noch kein Ausgang ist aktiviert.
GPIOs arbeiten mit 3,3 V. Das Relais muss einen ausgeschalteten/betriebslosen
ESP32 als fehlende Freigabe behandeln; insbesondere beim Booten sind externe
definierte Pegel erforderlich. Vor Anschluss am Schütz mit Kleinspannung prüfen.
Die Schütz-/PV-Verriegelung und Relaisdimensionierung muss eine Elektrofachkraft
anhand der realen Schaltung festlegen. Thermostat und Sicherheitstemperaturbegrenzer
bleiben unabhängig wirksam. Mischer-Richtungspaare werden nicht unterstützt.

## Sensoren

- DS18B20: bis zu acht Geräte an einem 1-Wire-Bus, Zuordnung über die feste
  64-Bit-Adresse. Dreiadrig mit 3,3 V, GND und DQ verwenden; für einen kurzen
  Testaufbau typischerweise 4,7 kΩ von DQ nach 3,3 V. Kabellänge/Topologie können
  Anpassungen erfordern. Kein Parasite-Power-Betrieb in dieser Firmware.
- I²C: SHT31 an 0x44/0x45 und BME280 an 0x76/0x77; SDA/SCL konfigurierbar.
- Nur für 3,3-V-Logik geeignete Sensorplatinen anschließen. Sensoren nach dem
  Anschließen durch Neustart neu erkennen lassen. Fehlende/ungültige Werte werden
  als nicht verfügbar angezeigt; keine erfundenen Temperaturen oder Nullwerte.

## Home Assistant

Im ESP-Webformular MQTT-Broker, Port und Zugangsdaten setzen. In Home Assistant
muss die MQTT-Integration eingerichtet sein. Danach erfolgt Discovery automatisch:

- WW-Zustand, ETA-Verbindung und Elektro-Freigabe
- aktivierte Digitaleingänge und WW-Ausgänge als Binary Sensors
- manuelle Ausgänge als zeitbegrenzte Switches
- DS18B20-/I²C-Temperaturen und ggf. Luftfeuchte

Topics liegen unter `etaio/<Gerätekennung>/`, Discovery unter `homeassistant/`.
Gespeicherte (retained) Schaltbefehle werden verworfen. Der Broker meldet das Gerät
bei Verbindungsverlust über Last Will offline. Alte Discovery-Einträge werden bei
Konfigurationswechsel gelöscht, sofern der bisherige Broker erreichbar ist;
andernfalls dort alte Entitäten manuell entfernen.

## WLAN-Wechsel und Sensornamen (1.1.0)

- Bis zu 3 WLANs werden gespeichert (Einstellungen → WLAN: „Nur merken“ / „Merken & jetzt verbinden“, Entfernen; das letzte bleibt). Beim Start zuerst das zuletzt benutzte, sonst das stärkste vorhandene gespeicherte; im Betrieb nach 60 s Verlust Wechsel auf ein anderes gespeichertes Netz. Über das Setup-WLAN eingerichtete Netze werden automatisch übernommen. Passwörter nur im Gerät (NVS `wifis`), nie in Status oder Export.
- Findet das Gerät nach dem Start 60 s lang keines der gespeicherten WLANs, öffnet es das Setup-WLAN
  `eta-io-<kennung>-Setup` (Passwort: Zugangskarte, Adresse http://192.168.4.1) für 5 Minuten; danach versucht es
  das gespeicherte WLAN erneut, dann wieder Setup-WLAN. Ein Verbindungsverlust im laufenden Betrieb öffnet es nicht.
- Hostname im Netz (Gerät & ETA): frei wählbar, nur a-z, 0-9 und Bindestrich (kein Unterstrich); leer = `eta-io-<kennung>`. Erreichbar als `http://<hostname>.local/` bzw. über den Router. Setup-WLAN und MQTT-Benutzer behalten die Gerätekennung.
- „WLAN zurücksetzen“ (Wartung) löscht die gespeicherten WLAN-Daten und startet neu; danach öffnet das Setup-WLAN.
- Sensornamen: unter Temperatursensoren je Adresse ein Name; er wird Teil des MQTT-Topics
  (`etaio/<benutzer>/<name>/temperature`, Umlaute ausgeschrieben). Übersicht aller Sensoren: `etaio/<benutzer>/sensors`.
- 1-Wire-Diagnose in der Oberfläche (Karte „1-Wire-Bus“): antwortende Geräte und erkannte DS18B20; gesucht wird nur beim Start.

## ETA schalten über die NAS (ab 1.1.0)

- MQTT zum NAS-Broker (EMQX): Host `mqtt.example.invalid`, Port 8883, TLS an. Die CA der NAS ist in
  `include/mqtt_ca.h` eingebaut. Benutzer = Gerätekonto im Broker **und** in der ETA-App
  (Personen & Zugänge, Häkchen „MQTT-Gerät“, Recht „Schalten“ für den Bereich).
- IO-Funktion **ETA-Schalter**: Ein Eingang setzt bei jeder stabilen Änderung (1 s) einen ETA-Punkt, z. B.
  `wiese-switch` auf `Ein`/`Aus`. Nach einem Neustart wird nichts gesendet; Änderungen während einer
  MQTT-Unterbrechung werden nachgereicht.
- **ETA-Schaltflächen**: bis zu 6 Knöpfe (Punkt + fester Wert) in der Weboberfläche, mit aktuellem Wert und
  Rückmeldung der App (bestätigt, abgewiesen, unklar).
- Der ESP schreibt nie direkt in die ETA; die App prüft Rechte, Freigabe und Wertebereich und liest zurück.
- Eigene Topics: `etaio/<benutzer>/…`; manuelle Ausgänge über `etaio/<benutzer>/ioN/set` mit `ON`/`OFF`.
- OTA: Web-OTA funktioniert im Browser; für Skripte ArduinoOTA (`espota.py -i <ip> -a <web-passwort> -f firmware.bin`).

## OTA und Zugangsschutz

Weboberfläche: unter Wartung die erzeugte **firmware.bin** hochladen. Kein
komplettes Flash-Abbild, keine partitions.bin und keinen Bootloader für Web-OTA
verwenden. Alternativ ArduinoOTA über das lokale Netzwerk, Gerätename wie oben,
Passwort gleich dem aktuellen Web-Passwort. Die Partitionierung enthält zwei
OTA-App-Slots. Konfiguration und WLAN bleiben bei passenden OTA-Updates erhalten.
Nach einem abgebrochenen OTA-Vorgang bleiben die Ausgänge bis zum Neustart aus.

Webzugriff verwendet die Digest-Authentifizierung der ESP32-WebServer-Bibliothek;
Mutationen benötigen zusätzlich einen Custom Header. Konfigurations-GET und Export
enthalten keine Passwörter. Die Weboberfläche, ArduinoOTA und MQTT sind für ein
vertrauenswürdiges lokales Netz vorgesehen, nicht für Portfreigaben ins Internet.
HTTP/MQTT-Nutzdaten sind nicht TLS-verschlüsselt. Ein OTA-Abbild enthält die
individuellen Bootstrap-Zugangsdaten: privat aufbewahren. Firmware ist nicht
signiert; Konfiguration/Zugangsdaten im Flash sind nicht verschlüsselt.

## Build und Prüfungen

```powershell
python scripts/provision.py
python -m platformio run
python -m platformio run --target upload
```

`scripts/provision.py` erzeugt einmalig zufällige Zugangsdaten und bewahrt sie für
weitere Builds. Das bestehende private Verzeichnis nicht versehentlich ersetzen.
Ein vollständiges Backup der ursprünglichen Firmware wird vor dem ersten Flashen
unter `private/` abgelegt. Auch dieses kann Geheimnisse enthalten.

Hosttests: `tests/logic_test.cpp` gegen TinyXML2 mit g++ bauen. UI-Tests:
`npm install --prefix private/ui-test playwright` und `node tests/ui_test.cjs`
(installierter Microsoft Edge erforderlich). UI-Testdaten sind simuliert.

## Referenzen

- [ETA REST API 1.2](https://www.meineta.at/javax.faces.resource/downloads/ETA-RESTful-v1.2.pdf.xhtml?ln=default&v=0)
- [WiFiManager](https://github.com/tzapu/WiFiManager)
- [DS18B20 Herstellerdaten](https://www.analog.com/en/products/ds18b20.html)
- [Home Assistant MQTT](https://www.home-assistant.io/integrations/mqtt/)
- [ArduinoMqttClient](https://github.com/arduino-libraries/ArduinoMqttClient)
