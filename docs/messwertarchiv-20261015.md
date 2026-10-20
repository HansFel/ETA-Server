# Messwertverdichtung ab 30 Tagen

Entscheidung 2026-10-15: 30 Tage volle Auflösung, danach 5-Minuten-Blöcke.

## Umsetzung

- `Sample` bleibt für aktuelle Messwerte unverändert. Ältere vollständig abgelaufene
  5-Minuten-Intervalle werden je Datenpunkt in `SampleArchive` gespeichert.
- Der Block enthält nach Einheit getrennte Mittelwerte, Minimum, Maximum und
  gültige/ungültige Anzahlen. Mittelwerte sind arithmetische Messwertmittel;
  sie ersetzen keine zeitgewichtete Auslastung oder Leistungsintegration.
- Die ursprünglichen Zeitpunkte (einschließlich Mikrosekunden), Werte, Rohwerte,
  Texte, Einheiten und Qualitätsstatus bleiben zusätzlich als komprimiertes JSON
  mit Versionskennung und SHA-256-Prüfsumme erhalten. Keine Pickle-Deserialisierung.
- Historie, Kennzahlen und CSV lesen beide Speicherformen gemeinsam. Daher bleiben
  Trapezintegration, Laufzeitdefinition, Zählerwechsel, Messlücken und beliebige
  Zeitraumgrenzen unverändert. Auch Detailansichten alter Zeiträume bleiben möglich.
  Die bisherige 31-Tage-Grenze pro Abfrage bleibt bestehen.
- Blöcke werden in UTC ausgerichtet. Sommerzeit erzeugt keine doppelten Zeitpunkte.
  Datenpunktrechte werden vor dem Lesen wie bisher je Anfrage geprüft.
- Ein gemeinsamer Archiv-Lock verhindert Mischstände bei gleichzeitigen Abfragen.
  Schreiben des geprüften Blocks und Löschen seiner bisherigen Einzelzeilen bilden
  eine Transaktion. Später eingetragene alte Werte werden atomar in den Block ergänzt.
- Der Worker verarbeitet zwischen Messzyklen höchstens 64 Blöcke je Durchlauf.
  Vor der ersten Verdichtung eines UTC-Tages entstehen eine konsistente Sicherung
  `pre-archive` und ein isolierter Restore-Test. Bei Fehlern keine weitere Verdichtung,
  sichtbarer Fehler im Admin-Dashboard, Wiederholung nach einer Stunde.
- Backups enthalten Archivblöcke; Restore-Tests prüfen zusätzlich alle komprimierten
  Inhalte. `pre-archive` unterliegt der bestehenden instanzbezogenen Aufbewahrung;
  die zuletzt verwendete Sicherheitssicherung wird zusätzlich geschützt.

## Konfiguration und Speicherplatz

`ARCHIVE_ENABLED=1`, `ARCHIVE_AFTER_DAYS=30` (mindestens 30). Mit
`ARCHIVE_ENABLED=0` pausiert nur die neue Verdichtung; Archive bleiben lesbar.
`python manage.py compact_samples --blocks 64` führt einen expliziten, begrenzten
Lauf unabhängig von der automatischen Aktivierung aus, mit derselben Sicherung.

Das Admin-Dashboard zeigt die Gesamtzahl erhaltener Messungen sowie Einzelzeilen,
archivierte Messungen und Archivblöcke getrennt. Die Gesamtzahl sinkt nicht, da die
Messungen erhalten bleiben. Bei 30-Sekunden-Abständen ersetzt ein Block normalerweise
zehn Tabellenzeilen. Das bedeutet nicht zehnfach weniger Dateigröße.

SQLite nutzt frei gewordene Seiten der laufenden Datenbank wieder. Die Dateigröße
auf dem Datenträger sinkt dadurch zunächst nicht. Neue Sicherungen werden auf ihrer
eigenen temporären Datei mit `VACUUM` verkleinert; die laufende Datenbank wird dabei
nicht für einen vollständigen `VACUUM` angehalten. Noch keine 30 Tage alten Daten
bedeuten noch keine Verdichtung. NAS-Datenalter in dieser Sitzung nicht geprüft.

## Verifikation (lokal, 2026-10-15)

- 43 Django-Tests erfolgreich, davon neun Archivtests: Ergebnisgleichheit für
  Diagramme/CSV/Kennzahlen, teilweise archivierte Zeiträume, späte Werte, UTC über
  Sommerzeitwechsel, fehlgeschlagener Restore, defekte Nutzdaten, Transaktionsabbruch
  nach Löschung, Rechteentzug und Wiederholungsverhalten.
- `makemigrations --check --dry-run`: keine fehlenden Migrationen.
- `scripts/verify_archive_storage.py` erzeugt ausschließlich eine temporäre Datenbank
  mit Testdaten. Ergebnis: 9.720 Messungen in 972 Blöcken, 0 verbleibende Einzelzeilen,
  alle Messungen wieder lesbar und isolierter Restore erfolgreich.
- Belegte Seiten der Messwerttabellen einschließlich ihrer Indizes: 1.368.064 auf
  569.344 Byte, 58,4 % weniger. Synthetischer Test, keine Prognose für Produktionsdaten.
- Gebautes Image: `eta-heiz-kontroller:archive-20261015`.

## Rollout und Rücknahme

### Mit `nas-deploy.sh`

Die Datei liegt in der Projektwurzel. Zusammen mit
`scripts/pre_archive_update_backup.py` in das bestehende NAS-Projekt kopieren;
das getestete Image muss dort bereits geladen sein. Im Projektverzeichnis:

```sh
bash nas-deploy.sh --check
bash nas-deploy.sh
```

Bei abweichendem Compose-Projektnamen bzw. Konfigurationsdateinamen beide Aufrufe
mit `--project NAME --compose DATEI` ergänzen. `--help` zeigt alle Optionen.
`--check` prüft die laufende Instanz und das Image ohne Sicherung, Migration oder
Neustart. Ein temporärer, vom Netzwerk getrennter Container prüft den Image-Inhalt.
Ohne `--include-rights-update` wird ein NAS ohne Personenrechte-Migration 0002
vor Änderungen abgewiesen. Am 2026-10-15 hat der Benutzer das gemeinsame Update
von Personenrechten, direktem Schalten und Archiv freigegeben. Dafür:

```sh
bash nas-deploy.sh --check --include-rights-update && bash nas-deploy.sh --include-rights-update
```

Der Upgrade-Pfad 0001 → 0003 wurde mit synthetischen Konten und Messwerten isoliert
geprüft: IDs und Passwort-Hashes unverändert, Administrator erhalten, Leser → Sehen,
Bediener → Schalten für sechs Bereiche, Messwerte erhalten, Restore erfolgreich.
Sechs simulierte Deploy-Ablaufprüfungen erfolgreich.

Die NAS-Compose-Datei hatte für `/backups` kein Volume. Das Skript exportiert daher
vor dem Containerwechsel vorhandene Sicherungen aus dem alten Worker, erhält deren
Dateieigentümer, prüft die Prüfsummen und bindet `./backups:/backups` in alle drei
Dienste ein. Abweichende bestehende Backup-Mounts werden zur Vermeidung einer
ungeprüften Umstellung abgewiesen. Die laufenden Dienste bleiben bis zur geprüften
Sicherung unverändert.

Der eigentliche Lauf sichert zuerst mit der bisherigen Anwendung und prüft einen
isolierten Restore. Danach werden Web/Worker angehalten, Init ausgeführt und beide
Dienste mit einer festgehaltenen Image-ID gestartet. Web-Antwort und ein neuer
Worker-Heartbeat werden geprüft. Loggerstatus und Speicherübersicht werden ausgegeben;
eine verbundene Heizung wird durch den Web-Healthcheck allein nicht nachgewiesen.

Nach Erfolg bindet `compose.deployed.yaml` dauerhaft das geprüfte Image ein.
Bei weiteren Compose-Aufrufen diese Datei zusätzlich verwenden. Konfigurationskopien
und vorherige Image-IDs liegen geschützt in `backups/deploy-*`. Die Vor-Update-DB
liegt zusätzlich im vom Sicherungsskript ausgegebenen Rollback-Verzeichnis.
Bei Fehler nach Beginn der Wartung bleiben Web und Worker angehalten; das Skript
spielt keine Datenbank automatisch zurück und löscht keine Volumes.

Geprüft: Bash-Syntax und fünf isolierte Ablaufprüfungen mit simuliertem Docker
(Erfolg, Prüfmodus, alter Schemastand, Sicherungsfehler, Migrationsfehler).
Kein produktiver NAS-Deploy in dieser Sitzung ausgeführt.

Noch nicht auf laufende lokale Instanzen oder die NAS ausgerollt. Gemäß
`C:/dev/_shared/nas-deploy.md` erfolgt die NAS-Ausführung durch Johann.

Vor Rollout aktuellen NAS-Quellstand abgleichen: Der lokale Checkout enthält auch
die Personenrechte-Änderung (Migration 0002). Das neue Image nicht ungeprüft über
einen älteren NAS-Stand legen. Bestehendes Image, Compose und Konfiguration sichern;
keine Volumes neu anlegen oder löschen.

1. Im bisherigen Worker unter Maintenance-/Backup-Lock `backups.snapshot('pre-update')`
   und `backups.restore_test(id)` ausführen. Die Sicherung samt Manifest außerhalb
   des automatischen Aufbewahrungslaufs im Rollback-Verzeichnis schützen.
2. Web und Worker anhalten. Das geprüfte Image für Init, Web und Worker eintragen.
3. Init (`migrate`, `bootstrap`, `collectstatic`) gegen das bestehende Datenvolume
   ausführen; Migration 0003 legt Tabelle und Altersindex an. Für den Index genügend
   freien Speicher bereithalten. Danach Web und Worker mit demselben Image starten.
4. Migration, aktuelle Livewerte, Workerstatus, historische Abfragen, Rechte und
   Sicherung/Restore prüfen. Die erste Verdichtung erzeugt eine weitere geprüfte
   Sicherung im neuen Schema. Bei Daten unter 30 Tagen bleibt sie zunächst untätig.

Für eine Instanz, deren bestehende Compose-Datei `compose.yaml` heißt, im
zugehörigen Projektverzeichnis (Projektname und NAS-Quellstand vorher abgleichen):

```sh
# Noch mit dem bisherigen Image; bei Fehler hier abbrechen.
docker compose exec -T worker python - < scripts/pre_archive_update_backup.py
# Geprüftes Image muss auf dem Zielhost bereits geladen sein.
docker compose stop web worker
docker compose -f compose.yaml -f compose.archive.yaml run --rm --no-deps init
# Nur nach erfolgreichem Init fortsetzen; gleiche Volumes und Instanz verwenden.
docker compose -f compose.yaml -f compose.archive.yaml up -d --no-deps web worker
docker compose -f compose.yaml -f compose.archive.yaml ps
docker compose -f compose.yaml -f compose.archive.yaml exec -T worker python manage.py worker_health
```

Für spätere Neustarts dieselben beiden Compose-Dateien verwenden. Sonst würde
wieder das alte Image aus der Basisdatei gewählt. Alternativ nach erfolgreicher
Abnahme die geprüfte Imagekennung dauerhaft in der Instanzkonfiguration setzen.

Rücknahme ohne Datenverlust: automatische Verdichtung mit `ARCHIVE_ENABLED=0`
pausieren und den neuen Archivleser beibehalten. Ein altes Image kann Archive nicht
lesen. Ein vollständiger Rücksprung benötigt das alte Image und die Vor-Update-DB;
dabei würden seitdem eingegangene Messwerte fehlen. Vorher aktuellen Stand sichern.
Migration 0003 nach erfolgter Verdichtung nicht rückwärts ausführen: Das würde die
Archivtabelle und darin enthaltene Messhistorie entfernen.
