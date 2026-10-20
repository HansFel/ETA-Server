# Persönliche Diagrammauswahl

NAS-Image: `eta-heiz-kontroller:chart-preferences-20261019`.

Die Übersicht speichert ausgewählte Messgrößen und den zuletzt gewählten relativen Zeitraum (1 Stunde, 24 Stunden, 7 Tage, 30 Tage) automatisch pro angemeldeter Person. Die Auswahl gilt auch nach neuer Anmeldung und auf anderen Geräten. Absolute Datumsgrenzen aus „Eigener Zeitraum“ werden nicht gespeichert. Unter dem Auswahlbereich steht der Speicherstatus; bei Versandfehlern wird eine verständliche Meldung angezeigt.

Speicherung in der vorhandenen Tabelle `State`, Schlüssel `dashboard-user-<Benutzer-ID>`, Inhalt ausschließlich Punkteschlüssel und Stundenwert. Kein Schema-Upgrade, keine fremde Benutzer-ID aus dem Request. `POST /api/dashboard-preferences/` benötigt Anmeldung, Bereichszugriff und gültiges CSRF-Token. Nur aktivierte, nicht steuernde, aktuell sichtbare Datenpunkte dürfen gespeichert werden. Gespeicherte Auswahl wird bei jedem Dashboard-Aufruf erneut gegen aktuelle Rechte gefiltert. Leere Auswahl wird ebenfalls gemerkt; das Diagramm fordert dann zur Auswahl einer Messgröße auf. Wenn alle früher ausgewählten Punkte durch Rechteänderung entfallen, wird eine zulässige Voreinstellung verwendet.

Browser bündelt Änderungen für 300 ms und sendet Schreibanfragen nacheinander, um vertauschte Speicherreihenfolge zu vermeiden. Auch „Verlauf anzeigen“ einer Sensorkarte speichert die neue Auswahl. Berechnete Temperaturdifferenzen sind auswählbar und werden weiterhin ausschließlich beim Diagrammabruf aus Originalmessungen errechnet.

Verifikation: 81 isolierte Django-Tests erfolgreich, davon vier neue Tests für erneute Anmeldung, getrennte Konten, Rechteentzug, leere Auswahl, Eingabeprüfung und CSRF. Headless-Edge mit synthetischen Daten auf 1440 und 390 Pixeln prüft automatisches Speichern inklusive CSRF-Header sowie Wiederherstellen von Auswahl und Zeitraum nach Neuladen; keine JavaScript-Fehler oder horizontale Überläufe. Artefakte unter `artifacts/chart-preferences/`.

Vor NAS-Rollout wurde eine Datenbanksicherung mit tatsächlichem isoliertem Restore geprüft. Geschützte Rollback-Konfiguration und vorherige Quelldateien: `/volume1/docker/instanzen/eta/backups/chart-preferences-20261019/`. `env.before` enthält Geheimnisse und darf nicht weitergegeben werden. Weiterhin Compose-Projekt `eta` mit `compose.yaml`, `compose.deployed.yaml`, `compose.mqtt.yaml` verwenden. Ein Rollback auf das vorherige Graph-only-Image lässt die persönlichen Einstellungen gespeichert, verwendet sie aber noch nicht.
