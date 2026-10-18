# Sicherungen {#backups}

`faden backup` sichert alles, was der Server braucht, um wieder in Betrieb zu gehen: jede Datenbank, die `.env`
mit ihren Schlüsseln und das Zertifikatsmaterial, dem die Maschinen vertrauen. `faden restore` stellt es wieder
her; die Wiederherstellung wird noch getestet (siehe [Wiederherstellen](#restore)). Bei der Installation wird eine nächtliche Sicherung eingerichtet.

## Was eine Sicherung enthält {#what-a-backup-holds}

| Teil | In der Sicherung | Warum es wichtig ist |
|---|---|---|
| Jede Datenbank im Postgres des Servers | Ja | Benutzer, Einstellungen, Provider, Maschinen, Cluster, Bereitstellungen, Chats, das Anfrageprotokoll |
| `.env` | Ja, außer mit `--db-only` | Sie enthält den Schlüssel, mit dem die Chats verschlüsselt sind, und die Schlüssel, die gespeicherte Geheimnisse verschlüsseln (das Hugging-Face-Token, geheime Einstellungen, die Zugangsdaten der MCP-Server). Eine Datenbank, die ohne ihre `.env` wiederhergestellt wird, enthält Chats und Geheimnisse, die niemand lesen kann |
| Zertifikatsmaterial (`tls/`, `tls-ca/`) | Ja, außer mit `--db-only` | Das ausgelieferte Zertifikat und die eigene Zertifizierungsstelle des Servers mit ihrem privaten Schlüssel. Damit ist ein neu aufgesetzter Server einer, dem die Maschinen weiterhin vertrauen |
| Migrationsversionen und Prüfsummen | Ja | `faden restore` prüft die Datenbank-Dumps, die Volume-Archive und die Zertifikatsdateien, bevor es etwas ändert |
| Dateien, die in die Wissensdatenbank hochgeladen wurden | Mit `--include-volumes` | Ohne diese Option enthält die Sicherung nur den daraus gelesenen Text, nicht die Dateien selbst. Ist `FADEN_DATA_DIR` gesetzt, liest die Sicherung den Ordner `knowledge` (und eine Wiederherstellung schreibt ihn) |
| Dateien, die an Chats angehängt wurden | Mit `--include-volumes` | Ohne diese Option liegt nur der daraus gelesene Text in der Datenbank, nicht die Dateien selbst. Ist `FADEN_DATA_DIR` gesetzt, liest die Sicherung den Ordner `chat-files` (und eine Wiederherstellung schreibt ihn) |
| Modelle | Nein | Sie lassen sich erneut herunterladen. Bewahren Sie eine Kopie der Modelle auf, die Sie importiert statt heruntergeladen haben |
| Logs, Metriken, Traces | Mit `--include-volumes`, außer Metriken | Sie werden zum Ansehen auf dem Server aufbewahrt; eine Sicherung ohne sie verliert nichts, was der Server braucht |
| Runtime-Images | Nein | `faden runtime-images` holt sie erneut |
| Die GPU-Maschinen | Nichts zu sichern | Sie halten keinen Zustand, den der Server braucht; ihre Cluster laufen weiter, wenn der Server verloren geht |

!!! warning
    Eine Sicherung enthält private Schlüssel und alle Geheimnisse der Installation. Verschlüsseln Sie sie (siehe
    [Verschlüsselte Sicherungen](#encrypted-backups)), behandeln Sie sie so vertraulich wie die `.env`, und bewahren
    Sie Kopien außerhalb des Servers auf: Eine Sicherung auf derselben Festplatte übersteht den Verlust dieser
    Festplatte nicht.

Auf dem Server kann eine Sicherung nur der Benutzer lesen, der `faden` ausführt, wie die `.env`; `faden deploy` und
`faden upgrade` tun dasselbe für Sicherungen von früher.

## Jetzt sichern {#back-up-now}

```bash
faden backup
```

Der Befehl fragt nach einer Bestätigung (`-y` überspringt sie), schreibt dann `~/fadenstack/backups/<time>/` und
listet auf, was er gesichert hat.

| Option | Was sie tut |
|---|---|
| `--output-dir PATH` | Die Sicherung woanders hinschreiben, z. B. auf eine eingehängte Sicherungsplatte. Ein relativer Pfad gilt innerhalb des Installationsverzeichnisses |
| `--retain N` | Nur die N neuesten Sicherungen dieser Art in diesem Verzeichnis behalten (Standard 5). Ältere derselben Art werden gelöscht |
| `--db-only` | Nur die Datenbanken, ohne `.env` und Zertifikatsmaterial. Bewahren Sie eine Kopie der `.env` getrennt auf, so vertraulich wie die Sicherungen |
| `--include-volumes` | Auch die Dateien, die in die Wissensdatenbank hochgeladen wurden, die an Chats angehängten Dateien, die Logs, die Traces und Grafanas eigene Daten. Nicht die Modelle |
| `-y` | Nicht nachfragen |

??? info "Wie `--retain` zählt"
    Jede Sicherung vermerkt, wer sie angelegt hat: der nächtliche Timer, Sie mit `faden backup` oder
    `faden upgrade`. `--retain` zählt und löscht nur Sicherungen der eigenen Art. Ein von Hand gestartetes
    `faden backup --retain 3` lässt die nächtlichen Sicherungen also in Ruhe, und ein Upgrade verdrängt sie nicht.
    Eine Sicherung von Hand behält 5, sofern Sie keine andere Zahl angeben, die nächtliche behält 7 (siehe
    unten), und jedes `faden upgrade` behält 5 eigene. Sicherungen aus früheren Versionen haben keine Art und
    zählen als von Hand; ein vor dieser Version eingerichteter nächtlicher Timer zählt weiter als von Hand, bis
    Sie `faden backup schedule` erneut ausführen.

## Die nächtliche Sicherung {#the-nightly-backup}

`faden deploy` bietet eine nächtliche Sicherung um 03:00 Uhr an, die die letzten 7 behält, und schaltet sie mit
`-y` ein. So ändern oder beenden Sie sie:

```bash
faden backup schedule --at 01:30 --retain 14
faden backup schedule --off
```

Ihre Ausgabe geht nach `~/fadenstack/backups/backup.log`. Lesen Sie dieses Log ab und zu: Eine Sicherung, die
nachts fehlschlägt, meldet das nur dort.

??? info "Wie die nächtliche Sicherung gestartet wird"
    Sie läuft als systemd-Timer Ihres Benutzers (`systemctl --user list-timers`) oder, wo es kein systemd gibt, aus
    Ihrer crontab.

Die nächtliche Sicherung schreibt nach `~/fadenstack/backups/`, auf dieselbe Festplatte wie die Daten. Kopieren
Sie die Sicherungen mit Ihren eigenen Werkzeugen woanders hin, oder planen Sie stattdessen
`faden backup -y --output-dir <your backup disk>` mit Ihrem eigenen Scheduler ein.

Jedes `faden upgrade` legt außerdem eine Sicherung an, bevor es etwas ändert.

## Verschlüsselte Sicherungen {#encrypted-backups}

Sobald die Installation einen Sicherungsschlüssel hat, wird jede Sicherung verschlüsselt. Jede Datei darin wird
für den öffentlichen Teil des Schlüssels verschlüsselt, der auf dem Server bleibt; die nächtliche Sicherung läuft
also von allein. Nur der **Wiederherstellungsschlüssel** öffnet eine Sicherung: Ohne ihn liest sie niemand, weder
wer die Sicherungsplatte hat noch wer diesen Server verwaltet. Fadenstack bewahrt keine Kopie davon auf.

`faden deploy` legt den Schlüssel an. `faden upgrade` bietet es einmal an, wenn jemand am Terminal sitzt;
`faden upgrade -y` lässt die Sicherungen, wie sie waren. So legen Sie ihn selbst an:

```bash
faden backup key create
```

1. Der Wiederherstellungsschlüssel wird einmal angezeigt: `FADEN-BACKUP-KEY1` und zwölf Zeichengruppen.
2. Bewahren Sie eine Kopie außerhalb des Servers auf, in einem Passwort-Manager oder auf Papier an einem sicheren
   Ort, und bestätigen Sie das.
3. Der Bildschirm wird geleert, und Sie werden nach zwei Gruppen des Schlüssels an zufälligen Stellen gefragt,
   aus Ihrer Kopie. Nach drei falschen Antworten wird der Schlüssel noch einmal von vorn gezeigt.

Mit `--write-to PATH` landet der Schlüssel stattdessen in einer Datei, etwa auf einem USB-Stick (nie im
Installationsordner), und wird zur Prüfung zurückgelesen.

??? info "Nach einer unbeaufsichtigten Installation (`faden deploy -y`)"
    Niemand ist da, der den Schlüssel sehen könnte. Er bleibt deshalb in `~/fadenstack/backup-recovery-key.txt`,
    lesbar nur für den Benutzer, der `faden` ausführt. Solange er dort liegt, liegt der Schlüssel auf demselben
    Server wie die Sicherungen: `faden backup`, `faden upgrade` und `faden backup key status` erinnern Sie daran,
    bis Sie ihn vom Server kopiert und `faden backup key confirm` ausgeführt haben. Der Befehl fragt nach zwei
    Gruppen aus Ihrer Kopie und löscht dann die Datei.

| Befehl | Was er tut |
|---|---|
| `faden backup key status` | Ob Sicherungen verschlüsselt werden, für welchen Schlüssel, und was noch zu tun ist |
| `faden backup key check` | Prüft, ob Ihre Kopie des Wiederherstellungsschlüssels noch die dieser Installation ist; nichts wird gespeichert. Einmal im Jahr und bei jedem Wiederherstellungstest |
| `faden backup key create --replace` | Ein neuer Schlüssel. Die bisherigen Sicherungen brauchen weiterhin den alten Wiederherstellungsschlüssel |

In einer verschlüsselten Sicherung bleibt nur eine kleine `backup.json` lesbar: wann sie angelegt wurde, von wem,
und welcher Schlüssel sie öffnet. Sicherungen aus der Zeit, bevor die Installation einen Schlüssel hatte, bleiben,
wie sie sind, und werden wie bisher wiederhergestellt; löschen Sie sie, sobald es verschlüsselte gibt.

!!! warning
    Ohne den Wiederherstellungsschlüssel lassen sich die damit angelegten Sicherungen nicht wiederherstellen, von
    niemandem.

## Wiederherstellen {#restore}

!!! warning "Wird noch getestet"
    Die Wiederherstellung wird noch getestet. Sie bringt die Daten zurück: die Datenbanken, die `.env`, das
    Zertifikatsmaterial und Volumes, wenn die Sicherung welche enthält. Die Dateien der Installation und das
    Release, mit dem die Sicherung angelegt wurde, bringt sie nicht zurück. Während sie läuft, sind die Dienste
    gestoppt und die Konsole zeigt die Wartungsseite (wie bei einem Upgrade), die sich neu lädt, sobald die
    Plattform wieder da ist. Probieren Sie eine
    Wiederherstellung an einer Kopie des Servers aus, bevor Sie sich auf eine verlassen.

```bash
faden restore ~/fadenstack/backups/<time>
```

Der Befehl fragt nach einer Bestätigung (`-y` überspringt sie). Eine verschlüsselte Sicherung fragt zuerst nach
dem Wiederherstellungsschlüssel.

??? info "Was die Wiederherstellung tut, der Reihe nach"
    1. öffnet eine verschlüsselte Sicherung mit dem Wiederherstellungsschlüssel, in einen Ordner in ihr, den nur Sie
       lesen können und der am Ende der Wiederherstellung gelöscht wird;
    2. zeigt das Datum der Sicherung, die Version des Werkzeugs, mit der sie angelegt wurde, und was sie enthält;
    3. prüft die Datenbank-Dumps, die Volume-Archive und die Zertifikatsdateien gegen die Prüfsummen in der Sicherung und bricht ab,
       wenn eine nicht passt;
    4. fragt nach einer Bestätigung (`-y` überspringt sie);
    5. schaltet die Wartungsseite ein, stoppt alle Dienste außer dem Eingangstor (nginx), stellt jede Datenbank
       und, sofern nicht anders angegeben, die `.env` und das Zertifikatsmaterial wieder her;
    6. startet alles wieder und nimmt die Wartungsseite wieder weg, auch wenn die Wiederherstellung fehlschlägt.

| Option | Was sie tut |
|---|---|
| `--verify-only` | Die Prüfsummen der Sicherung prüfen, nichts wiederherstellen |
| `--db-only` | Nur die Datenbanken wiederherstellen |
| `--skip-env` | Die aktuelle `.env` behalten |
| `--key-file PATH` | Den Wiederherstellungsschlüssel aus dieser Datei lesen, statt nach ihm zu fragen |
| `-y` | Nicht nachfragen |

!!! warning
    Eine Wiederherstellung ersetzt die aktuellen Datenbanken. Alles, was seit der Sicherung geschehen ist, geht
    verloren: Chats, Benutzer, Einstellungen, seitdem genehmigte Maschinen.

### Eine Sicherung prüfen {#check-a-backup}

Führen Sie ab und zu `faden restore --verify-only` mit einer Kopie einer Sicherung aus. Der Befehl zeigt Ihnen,
ob die Datenbank-Dumps, die Volume-Archive und die Zertifikatsdateien vollständig und unverändert sind, ohne die Installation
anzutasten.

## Einen verlorenen Server neu aufbauen {#rebuild-a-lost-server}

Die Wiederherstellung wird noch getestet: Probieren Sie sie zuerst an einer Kopie des Servers aus (siehe
[Wiederherstellen](#restore)).

1. Installieren Sie mit `faden deploy` einen neuen Server mit demselben Release, mit dem die Sicherung angelegt
   wurde (`faden restore --verify-only` zeigt es an).
2. Kopieren Sie die Sicherung darauf und führen Sie `faden restore` mit ihrem Pfad aus, bei einer verschlüsselten
   Sicherung mit dem Wiederherstellungsschlüssel. Der neue Server verschlüsselt seine eigenen Sicherungen für den
   Schlüssel, den sein `faden deploy` angelegt hat.
3. Hat der neue Server eine andere Adresse, führen Sie auf jeder Maschine die Installationszeile aus seiner
   Konsole erneut aus, damit sich die Agenten mit ihm verbinden (siehe [GPU-Maschinen](machines.md)).
4. Führen Sie `faden runtime-images` aus, wenn die Maschinen Runtime-Images brauchen werden, die der Server noch
   nicht vorhält.
5. Cluster, die nach der Sicherung erstellt wurden, sind nicht darin enthalten, aber ihre Maschinen führen sie
   weiter aus. Übernehmen Sie sie, statt sie neu zu erstellen: siehe [Cluster übernehmen](taking-over.md).

Weil die Sicherung die Zertifizierungsstelle enthält, vertrauen Maschinen, die dem alten Server vertraut haben,
auch dem neuen.
