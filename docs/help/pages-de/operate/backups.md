# Sicherungen {#backups}

`faden backup` sichert alles, was der Server braucht, um wieder in Betrieb zu gehen: jede Datenbank, die `.env`
mit ihren Schlüsseln und das Zertifikatsmaterial, dem die Maschinen vertrauen. `faden restore` stellt es wieder
her. Bei der Installation wird eine nächtliche Sicherung eingerichtet.

## Was eine Sicherung enthält {#what-a-backup-holds}

| Teil | In der Sicherung | Warum es wichtig ist |
|---|---|---|
| Jede Datenbank im Postgres des Servers | Ja | Benutzer, Einstellungen, Provider, Maschinen, Cluster, Bereitstellungen, Chats, das Anfrageprotokoll |
| `.env` | Ja, außer mit `--db-only` | Sie enthält den Schlüssel, mit dem die Chats verschlüsselt sind, und den Schlüssel, der gespeicherte Geheimnisse verschlüsselt (API-Schlüssel der Provider, das Hugging-Face-Token). Eine Datenbank, die ohne ihre `.env` wiederhergestellt wird, enthält Chats und Geheimnisse, die niemand lesen kann |
| Zertifikatsmaterial (`tls/`, `tls-ca/`) | Ja, außer mit `--db-only` | Das ausgelieferte Zertifikat und die eigene Zertifizierungsstelle des Servers mit ihrem privaten Schlüssel. Damit ist ein neu aufgesetzter Server einer, dem die Maschinen weiterhin vertrauen |
| Migrationsversionen und Prüfsummen | Ja | `faden restore` prüft jede Datei, bevor es etwas ändert |
| Modelle | Nein | Sie lassen sich erneut herunterladen. Bewahren Sie eine Kopie der Modelle auf, die Sie importiert statt heruntergeladen haben |
| Logs, Metriken, Traces | Nein | Sie werden zum Ansehen auf dem Server aufbewahrt, nicht wiederhergestellt |
| Runtime-Images | Nein | `faden runtime-images` holt sie erneut |
| Die GPU-Maschinen | Nichts zu sichern | Sie halten keinen Zustand, den der Server braucht; ihre Cluster laufen weiter, wenn der Server verloren geht |

!!! warning
    Eine Sicherung enthält private Schlüssel und alle Geheimnisse der Installation. Behandeln Sie Sicherungen so
    vertraulich wie die `.env`, und bewahren Sie Kopien außerhalb des Servers auf: Eine Sicherung auf derselben
    Festplatte übersteht den Verlust dieser Festplatte nicht.

## Jetzt sichern {#back-up-now}

```bash
faden backup
```

Der Befehl fragt nach einer Bestätigung (`-y` überspringt sie), schreibt dann `~/fadenstack/backups/<time>/` und
listet auf, was er gesichert hat.

| Option | Was sie tut |
|---|---|
| `--output-dir PATH` | Die Sicherung woanders hinschreiben, z. B. auf eine eingehängte Sicherungsplatte. Ein relativer Pfad gilt innerhalb des Installationsverzeichnisses |
| `--retain N` | Nur die N neuesten Sicherungen in diesem Verzeichnis behalten (Standard 5). Ältere dort werden gelöscht |
| `--db-only` | Nur die Datenbanken, ohne `.env` und Zertifikatsmaterial. Bewahren Sie eine Kopie der `.env` getrennt auf, so vertraulich wie die Sicherungen |
| `-y` | Nicht nachfragen |

!!! note
    `--retain` zählt jede Sicherung im Verzeichnis, auch die nächtlichen und die, die Upgrades anlegen. Ein von
    Hand gestartetes `faden backup` in das Standardverzeichnis behält 5, sofern Sie keine größere Zahl angeben.

## Die nächtliche Sicherung {#the-nightly-backup}

`faden deploy` bietet eine nächtliche Sicherung um 03:00 Uhr an, die die letzten 7 behält, und schaltet sie mit
`-y` ein. So ändern oder beenden Sie sie:

```bash
faden backup schedule --at 01:30 --retain 14
faden backup schedule --off
```

Sie läuft als systemd-Timer Ihres Benutzers (`systemctl --user list-timers`) oder, wo es kein systemd gibt, aus
Ihrer crontab. Ihre Ausgabe geht nach `~/fadenstack/backups/backup.log`. Lesen Sie dieses Log ab und zu: Eine
Sicherung, die nachts fehlschlägt, meldet das nur dort.

Die nächtliche Sicherung schreibt nach `~/fadenstack/backups/`, auf dieselbe Festplatte wie die Daten. Kopieren
Sie die Sicherungen mit Ihren eigenen Werkzeugen woanders hin, oder planen Sie stattdessen
`faden backup -y --output-dir <your backup disk>` mit Ihrem eigenen Scheduler ein.

Jedes `faden upgrade` legt außerdem eine Sicherung an, bevor es etwas ändert.

## Wiederherstellen {#restore}

!!! warning "Wird noch getestet"
    Die Wiederherstellung wird noch getestet. Sie bringt die Daten zurück: die Datenbanken, die `.env`, das
    Zertifikatsmaterial und Volumes, wenn die Sicherung welche enthält. Die Dateien der Installation und das
    Release, mit dem die Sicherung angelegt wurde, bringt sie nicht zurück. Während sie läuft, sind die Dienste
    gestoppt und die Konsole antwortet nicht: Eine Wartungsseite gibt es noch nicht. Probieren Sie eine
    Wiederherstellung an einer Kopie des Servers aus, bevor Sie sich auf eine verlassen.

```bash
faden restore ~/fadenstack/backups/<time>
```

Die Wiederherstellung:

1. zeigt das Datum der Sicherung, die Version des Werkzeugs, mit der sie angelegt wurde, und was sie enthält;
2. prüft jede Datei gegen die Prüfsummen in der Sicherung und bricht ab, wenn eine nicht passt;
3. fragt nach einer Bestätigung (`-y` überspringt sie);
4. stoppt die Dienste, stellt jede Datenbank und, sofern nicht anders angegeben, die `.env` und das
   Zertifikatsmaterial wieder her;
5. startet alles wieder.

| Option | Was sie tut |
|---|---|
| `--verify-only` | Die Prüfsummen der Sicherung prüfen, nichts wiederherstellen |
| `--db-only` | Nur die Datenbanken wiederherstellen |
| `--skip-env` | Die aktuelle `.env` behalten |
| `-y` | Nicht nachfragen |

!!! warning
    Eine Wiederherstellung ersetzt die aktuellen Datenbanken. Alles, was seit der Sicherung geschehen ist, geht
    verloren: Chats, Benutzer, Einstellungen, seitdem genehmigte Maschinen.

### Eine Sicherung prüfen {#check-a-backup}

Führen Sie ab und zu `faden restore --verify-only` mit einer Kopie einer Sicherung aus. Der Befehl zeigt Ihnen,
ob die Dateien vollständig und unverändert sind, ohne die Installation anzutasten.

## Einen verlorenen Server neu aufbauen {#rebuild-a-lost-server}

1. Installieren Sie mit `faden deploy` einen neuen Server mit demselben Release, mit dem die Sicherung angelegt
   wurde (`faden restore --verify-only` zeigt es an).
2. Kopieren Sie die Sicherung darauf und führen Sie `faden restore` mit ihrem Pfad aus.
3. Hat der neue Server eine andere Adresse, führen Sie auf jeder Maschine die Installationszeile aus seiner
   Konsole erneut aus, damit sich die Agenten mit ihm verbinden (siehe [GPU-Maschinen](machines.md)).
4. Führen Sie `faden runtime-images` aus, wenn die Maschinen Runtime-Images brauchen werden, die der Server noch
   nicht vorhält.
5. Cluster, die nach der Sicherung erstellt wurden, sind nicht darin enthalten, aber ihre Maschinen führen sie
   weiter aus. Übernehmen Sie sie, statt sie neu zu erstellen: siehe [Cluster übernehmen](taking-over.md).

Weil die Sicherung die Zertifizierungsstelle enthält, vertrauen Maschinen, die dem alten Server vertraut haben,
auch dem neuen.
