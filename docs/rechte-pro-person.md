# Rechte pro Person (Stand 2026-10-14)

Implementiert und lokal getestet (34 Django-Tests; die fünf Backup-Tests nur mit einer Shim für `hashlib.file_digest`,
weil die lokale Testumgebung Python 3.10 hat, die App-Images aber 3.13). **Nicht produktiv aktiviert.**

## Modell

- **Administrator** (Gruppe `admin`): darf alles; die Bereichsrechte werden ignoriert. Der letzte aktive Administrator bleibt geschützt.
- **Alle anderen Personen:** pro Bereich `Kein Zugriff` (kein Eintrag), `Sehen` (`view`) oder `Schalten` (`control`, schließt Sehen ein).
- Bereiche = `Point.section`: Kessel, HK Haus, Puffer, WW (Anzeige „Warmwasser“), Wiesenegger, Werkstatt (`heating/rights.py`).
- Rechte werden bei **jedem Request** neu aus der Datenbank gelesen; Entzug und Sperren wirken sofort.
- Keine Rollenauswahl mehr; eine Person hat genau die ihr zugewiesenen Rechte.

## Was eine Person sieht

- Übersicht, Live-Werte, Verlauf, Export: nur Datenpunkte aus Bereichen mit mindestens „Sehen“. Fremde Punkte → HTTP 403 (auch direkt per URL).
- Auswertung (Bilanz, Anlagenmeldungen): nur mit „Sehen“ im Bereich Kessel.
- „Heizung steuern“: nur Bereiche mit „Schalten“. Das Befehlsprotokoll zeigt nur eigene Befehle (Administrator: alle).
- Verwaltung (Personen, Datensicherung, Administration): nur Administrator.

## Schalten

1. Rechteprüfung für den Bereich des Punktes, globale Freigabe, Limit (30 pro 15 min je Person).
2. Wertebereich aus `varinfo`, aktueller Wert wird gelesen.
3. Punkte mit `Point.confirm`: einfache Ja/Nein-Seite, **ohne Passwort**.
4. Schreiben, Rücklesen, Befehlsprotokoll (`Command`), Audit (`control-request`, `control-result`). Kein automatischer Neuversuch.

Der Administrator stellt die Rückfrage je Punkt auf der Seite „Heizung steuern“ ein. Die globale Freigabe (mit Passwort) bleibt.

## Migration 0002

Bediener erhalten „Schalten“, Leser „Sehen“ für alle Bereiche, Administratoren bleiben Administratoren; die Gruppen
`operator`/`reader` werden entfernt. Die Rückfrage wird für die Standardpunkte eingeschaltet. Mit einer Kopie der Sicherung
vom 2026-10-14 geprüft (`admin`, `hans` bleiben Administratoren).

## Offen

- Für reine Wiesenegger-Steuerkonten (einziger schaltbarer Bereich: Wiesenegger) ist der Bedienumfang auf
  `wiese-switch` und die Wochenschaltuhr-Punkte (`wiese-time-*`) begrenzt. Ein direkter POST auf andere
  Wiesenegger-Steuerpunkte (z. B. `wiese-auto`, Heizkurven) wird serverseitig mit HTTP 403 abgewiesen.
- Zusätzlich gilt ein globaler Wiesenegger-Steuermodus:
  - `permanent`: Schalten gemäß Personenrechten immer möglich.
  - `esp_release`: Schalten nur bei aktiver, frischer ESP32-Freigabe (`wiesenegger-esp-release`).
  Die Einstellung erfolgt durch Administratoren auf der Steuerseite.
- Rate-Limit zählt weiter nach `REMOTE_ADDR` (hinter Traefik gleiche Adresse); Steuerbefehle zählen zusätzlich je Person.
- Einladungslink mit Unterpfad `/eta` und Audit für Anmeldungen: siehe `docs/auth-abgleich-20261012.md`.
- Vor dem NAS-Deployment: Sicherung, isolierter Restore-Test, danach Migration. Altstand der geänderten Dateien liegt in `_pre-rechte-20261014/`.
