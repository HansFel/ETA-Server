# Validierung â€“ 23.09.2026

## Implementiert

- WiFiManager mit individuellem WPA-Setup-Passwort und zeitlich begrenztem Portal
- Digest-geschÃ¼tzte WeboberflÃ¤che, KonfigurationsprÃ¼fung, acht IO-PlÃ¤tze
- Direkte, ausschlieÃŸlich lesende ETA-WW-Abfrage mit Sperre bei unbekannten Daten
- Digitale EingÃ¤nge, zeitbegrenzte manuelle AusgÃ¤nge und automatische WW-Freigaben
- DS18B20 (1-Wire), SHT31/BME280 (IÂ²C)
- MQTT Home Assistant Discovery, Availability, Schutz vor retained Schaltbefehlen
- Web-OTA und ArduinoOTA mit Zugangsschutz und Abschaltung der AusgÃ¤nge
- Geheimnisfreier Konfigurationsexport und Importvorschau

## Erfolgreich geprÃ¼ft

- ESP32-Build: 1.250.032 Bytes Firmware-Abbild, ca. 63 % des OTA-App-Slots,
  statische RAM-Belegung ca. 18 %.
- C++-Tests: gÃ¼ltige/ungÃ¼ltige ETA-Antworten, falsche Datenpunktadresse,
  unbekannte ZustÃ¤nde, Ablaufgrenze, Ãœberlauf von millis(), manuelle Zeitgrenze,
  Ausschluss von Boot-/Flash-/UART-Pins.
- Automatisierter Edge-Test mit simulierten API-Daten: Desktop- und Mobilansicht,
  acht IO-Zeilen, Live-Temperatur, WW-Sperranzeige, Speichern von IO und Sensorbus,
  Export ohne MQTT-Passwort; keine JavaScript-Fehler oder horizontales Ãœberlaufen
  des mobilen Seitenlayouts. Screenshots unter private/.
- Reales GerÃ¤t: ESP32-D0WDQ6, Revision 1.0, 4 MB Flash, MAC 30:ae:a4:37:98:18.
- Vorheriger Flash vollstÃ¤ndig gesichert (4.194.304 Bytes); SHA-256 im privaten
  Sicherungsmanifest. Keine Sicherung der Daten nur durch Annahme ersetzt.
- Firmware, Bootloader und Partitionen via COM4 geschrieben, Hashkontrolle durch
  esptool erfolgreich. GerÃ¤t bootet, NVS verfÃ¼gbar, alle acht IOs deaktiviert,
  geschÃ¼tzter Setup-AP `eta-io-189837-Setup` aktiv; seriell bestÃ¤tigt.

## Noch nicht am realen GerÃ¤t geprÃ¼ft

- WLAN-Verbindung und ETA-Abfrage vom ESP32 (WLAN-Einrichtung durch Benutzer offen)
- HTTP-Authentifizierung und Konfigurations-API auf dem ESP32
- OTA-Ãœbertragung Ã¼ber WLAN
- Home Assistant/MQTT-Ende-zu-Ende-Verbindung (Brokerdaten offen)
- Sensoren und Relais: noch keine Hardware angeschlossen

Es wurden keine Heizungsparameter verÃ¤ndert und keine SchÃ¼tzsteuerung aktiviert.
