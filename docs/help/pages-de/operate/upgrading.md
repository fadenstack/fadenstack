# Upgrade {#upgrading}

So bringen Sie den Server auf ein neues Release, danach die Agenten auf den GPU-Maschinen, und so stellen Sie
einen Cluster auf eine neuere Runtime um. Jeder dieser Schritte ist eigenständig und wird von Ihnen gestartet;
nichts aktualisiert sich von selbst.

| Was | Wie | Was es unterbricht |
|---|---|---|
| Der Server | `faden upgrade` | Die Konsole und das Gateway, für die Minuten, in denen die Dienste neu starten. Die Modelle laufen weiter |
| Der Agent einer Maschine | Die Installationszeile auf der Maschine erneut ausführen | Nichts: Der Agent startet neu, die Modelle laufen weiter |
| Die Runtime eines Clusters | **Laufzeit** auf der Seite des Clusters | Jedes Modell auf diesem Cluster, solange es neu lädt |

## Bevor Sie beginnen {#before-you-start}

- **Freier Speicherplatz.** Die neuen Images brauchen einige GB, ein neues Runtime-Image bis zu etwa 15 GB pro
  Maschinenart, und die Sicherung, die das Upgrade anlegt, braucht Platz für die Datenbanken. Prüfen Sie das mit
  `df -h`.
- **Wissen, für welche Installation das Werkzeug eingerichtet ist:** `faden config show` nennt sie
  (`install_dir`).
- **Einen Zeitpunkt wählen.** Während die Dienste neu starten, sehen die Benutzer eine Wartungsseite.

## Den Server aktualisieren {#upgrade-the-server}

1. Aktualisieren Sie das Kommandozeilenwerkzeug. Es bringt das Release mit:

    ```bash
    pipx upgrade fadenstack        # oder: uv tool upgrade fadenstack
    ```

2. Sehen Sie sich an, was das Upgrade tun würde:

    ```bash
    faden upgrade --dry-run
    ```

    Es nennt das Release, auf das es wechselt (`Release: <current> -> <new> (pull published images)`).

3. Führen Sie es aus:

    ```bash
    faden upgrade -y
    ```

4. Prüfen Sie das Ergebnis (siehe unten).

??? info "Was das Upgrade tut, der Reihe nach"
    1. prüft Docker und stellt sicher, dass kein Container, den es gleich ersetzt, von etwas anderem angelegt
       wurde;
    2. sichert jede Datenbank, die `.env` und das Zertifikatsmaterial nach `~/fadenstack/backups/<time>/` und
       bricht ab, wenn sich nichts sichern ließ;
    3. ersetzt die Dateien der Installation durch die des neuen Release und ergänzt in der `.env` nur die
       Einstellungen, die das Release neu einführt: Jeder Wert, den Sie gesetzt haben, bleibt erhalten, außer der
       Bemessung der Dienste, die wie bei `faden tune` neu für den Host berechnet wird;
    4. lädt die Images des neuen Release herunter;
    5. startet die Dienste hinter der Wartungsseite neu; hier laufen die Datenbankmigrationen;
    6. wartet, bis die Konsolen-API antwortet, und hebt dann die Wartungsseite auf;
    7. holt die Runtime-Images, die das neue Release nennt. Nur was sich geändert hat, wird heruntergeladen. Ein
       fehlgeschlagener Download hält nichts auf: Wiederholen Sie ihn mit `faden runtime-images`.

| Option | Was sie tut |
|---|---|
| `--dry-run` | Zeigen, was passieren würde, nichts ändern |
| `-y` | Vor dem Start nicht nachfragen |
| `--backup-dir PATH` | Die Sicherung vor dem Upgrade woanders ablegen |
| `--no-backup` | Die Sicherung überspringen. Nur, wenn Sie eine aktuelle haben |
| `--runtime-images ARCHS` | Welche Runtime-Images geholt werden: `all`, `none`, `x86_64`, `aarch64`. Bleibt gespeichert |

!!! warning
    Ein Upgrade geht nur vorwärts: Die Datenbanken werden auf das neue Release migriert. Das Werkzeug weigert
    sich, eine Installation auf ein älteres Release zu bringen, und zum vorherigen Release zurückzugehen ist noch
    nicht möglich (siehe [Zurückgehen](#going-back)).

Änderungen, die Sie an den Dateien der Installation selbst vorgenommen haben, z. B. an `nginx/nginx.conf`, werden
ersetzt. Halten Sie Ihre Änderungen in der `.env`.

### Was die Benutzer währenddessen sehen {#what-people-see-meanwhile}

![Die Wartungsseite: Fadenstack wird aktualisiert und lädt sich von selbst neu, sobald es wieder da ist](../images/operate/maintenance-page.png)

| Wer | Was er sieht, während die Dienste neu starten |
|---|---|
| Personen in der Konsole | Jede Seite der Konsole zeigt „Fadenstack wird aktualisiert“ und lädt sich selbst wieder in die Konsole, sobald sie zurück ist. |
| Skripte und SDKs | Die API (`/api`) und das Gateway (`/v1`) antworten mit `503` und `Retry-After: 30`, sodass Skripte und SDKs warten und es erneut versuchen können. |
| Eine offen gelassene Konsolenseite | Sie bemerkt die neue Version und bietet **Neu laden** an; eine Seite mit eingegebenem, aber nicht gesendetem Text wird nie von selbst neu geladen. |

Die Modelle auf den Clustern laufen die ganze Zeit weiter: Ein Cluster läuft auf seinen Maschinen, nicht auf dem
Server.

### Wartung von Hand {#maintenance-by-hand}

Dieselbe Wartungsseite können Sie für Ihre eigenen Arbeiten einschalten. Die Dienste laufen weiter; nur der
Eingang ändert sich.

```bash
faden maintenance on -m "Back at 15:00"   # der Hinweis erscheint auf der Seite
faden maintenance status
faden maintenance off
```

Ein Upgrade lässt eine Wartung, die Sie von Hand eingeschaltet haben, eingeschaltet.

### Prüfen {#check-it}

```bash
faden status                                           # alle Dienste laufen; gesund, wo es eine Prüfung gibt
curl --cacert ~/fadenstack/tls/ca.crt https://ai.example.internal/api/health   # 200
```

Melden Sie sich dann an der Konsole an. Maschinen und Cluster sind wie zuvor.

## Die Agenten der Maschinen aktualisieren {#update-the-machines-agents}

Ein neues Release kann einen neueren Node-Agenten erwarten, als auf den Maschinen läuft. Die Liste **Maschinen**
und die Seite jeder Maschine zeigen dann bei dieser Maschine **Agent *Version* verfügbar**, mit der Version, die
der Server erwartet. Agenten aktualisieren sich nie selbst, und der Server aktualisiert sie nie: Sie wählen das
Zeitfenster.

![Der Bereich „Maschine hinzufügen“ mit der Installationszeile zum Kopieren](../images/operate/add-machine-panel.png){ width="360" }

1. Öffnen Sie in der Konsole **Maschinen**, wählen Sie **Maschine hinzufügen** und kopieren Sie die
   Installationszeile.
2. Führen Sie die Zeile auf jeder Maschine erneut aus, genauso wie beim ersten Mal: mit `sudo` bei einem Agenten,
   der als Systemdienst läuft, ohne `sudo` bei einem, der als Benutzerdienst läuft.

Das Installationsprogramm lädt den Agenten herunter, den der Server jetzt erwartet, erkennt, dass die Maschine
bereits Mitglied ist, und startet den Agenten mit dem neuen Build neu. Es gibt nichts zu genehmigen. Die Modelle
auf der Maschine laufen weiter.

??? info "Warum die Modelle weiterlaufen"
    Der Agent ist die Verbindung der Maschine zum Server, und die Container des Clusters hängen nicht davon ab, dass
    er läuft.

Aktualisieren Sie die Agenten bald nach einem Upgrade. Manche Funktionen brauchen einen aktuellen Agenten auf
jeder Maschine eines Clusters, und die Konsole nennt die Maschinen, die aktualisiert werden müssen, wenn einer
fehlt (z. B. auf der Karte **Verkehr zwischen den Maschinen** des Clusters oder im Plan zum Leeren einer
Maschine).

### Maschinen ohne Internetzugang {#machines-without-internet-access}

Maschinen ohne Internetzugang installieren den Agenten aus der eigenen Kopie des Servers in
`~/fadenstack/agent-binaries/`. Ersetzen Sie diese Dateien nach jedem Server-Upgrade durch die Builds des neuen
Release (`faden-agent-linux-x86_64`, `faden-agent-linux-aarch64`, mit ihren `.sha256`-Dateien) von der
Release-Seite des Projekts. Nichts ersetzt sie für Sie, und eine alte Kopie hält die Maschinen zurück, die darauf
angewiesen sind.

## Einen Cluster auf eine andere Runtime umstellen {#move-a-cluster-to-another-runtime}

Eine Runtime ist das Image, das die Maschinen eines Clusters ausführen: Ray und vLLM zusammen, gebaut für
diese Maschinenart. Ist eine neuere zertifizierte Runtime verfügbar, zeigt die Karte **Laufzeit** des Clusters das
an. Nichts ändert sich, bis Sie umschalten.

![Die Karte „Laufzeit“ auf der Seite eines Clusters: die Runtime, mit der er läuft, sowie „Umschalten auf“ mit „Umschalten“](../images/operate/cluster-runtime-card.png)

1. Öffnen Sie **Cluster** und dann den Cluster.
2. Wählen Sie auf der Karte **Laufzeit** unter **Umschalten auf** die Runtime aus und wählen Sie **Umschalten**.
3. Lesen Sie die Warnung und wählen Sie **Mit dieser Laufzeit neu starten**.

Der Container jeder Maschine wird aus dem neuen Image neu erstellt (eine Maschine, die es noch nicht hat, holt es
zuerst), und jedes Modell auf dem Cluster lädt neu. Planen Sie ein Zeitfenster dafür ein.

??? info "Mehr zu Runtimes"
    Die Karte **Laufzeit** des Clusters zeigt, ob die Runtime zertifiziert ist: **Zertifiziert**, **Teilweise
    zertifiziert** oder **Nicht zertifiziert**. Ein Release liefert für jede Maschinenart eine als **Empfohlen**
    markierte Runtime, kann eine neuere **Vorschau** mitliefern und behält eine ältere, zu der Sie zurückkehren
    können. Ein neuer Cluster bekommt die empfohlene.

    Ein Cluster behält die Runtime, mit der er gestartet wurde: über Neustarts hinweg, beim Wiederanlaufen nach
    einer Störung und bei Server-Upgrades, die eine neuere mitbringen.

    Eine **Vorschau** führt Modelle aus, die die empfohlene Runtime nicht ausführen kann; wählen Sie sie für einen
    Cluster, der diese Modelle braucht. Der Modell-Marktplatz zeigt **Läuft hier nicht** bei einem Modell, das eine
    neuere Runtime braucht, als der Cluster ausführt (siehe [Modelle](../administer/models.md)).

## Zurückgehen {#going-back}

!!! warning "Wird noch getestet"
    Das Zurückgehen nach einem Upgrade wird noch getestet und führt nur einen Teil des Weges zurück. Eine
    Wiederherstellung bringt die Daten auf den Stand vor dem Upgrade, aber der Server bleibt auf dem neuen
    Release: Seine Programme und die Dateien der Installation bleiben, wie das Upgrade sie hinterlassen hat, und
    die Datenbanken werden beim Start der Dienste erneut auf das neue Release migriert. Zum früheren Release
    selbst zurückzugehen ist noch nicht möglich. Der sichere Weg zurück ist ein Snapshot des ganzen Servers,
    aufgenommen vor dem Upgrade: ein Snapshot der virtuellen Maschine oder ein Festplatten-Image.

Jedes Upgrade hinterlässt eine Sicherung in `~/fadenstack/backups/<time>/`. Um die Daten auf den Stand vor dem
Upgrade zurückzubringen, stellen Sie diese Sicherung wieder her:

```bash
faden restore ~/fadenstack/backups/<time>
```

Die Wiederherstellung spielt jede Datenbank, die `.env` und das Zertifikatsmaterial zurück und startet die
Dienste neu. Alles, was nach der Sicherung geschehen ist, geht verloren. Ein Cluster, der nach der Sicherung
erstellt wurde, läuft auf seinen Maschinen weiter; übernehmen Sie ihn, statt ihn neu zu erstellen (siehe
[Cluster übernehmen](taking-over.md)). Mehr zur Wiederherstellung: [Sicherungen](backups.md#restore).

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **`These containers have names this installation uses, but it did not create them`** | Ein Container mit einem der Namen von Fadenstack wurde auf andere Weise angelegt, von Hand oder von einem anderen Compose-Projekt. Die Meldung listet diese Container auf, zusammen mit dem Befehl, um sie zu entfernen (`docker rm -f <name>`). Beim Entfernen eines Containers bleiben seine Daten-Volumes erhalten. Führen Sie das Upgrade danach erneut aus. |
| **`This CLI (<version>) is older than the install (<version>).`** | Das Werkzeug ist älter als das Release, mit dem die Installation läuft. Aktualisieren Sie zuerst das Werkzeug. |
| **`Backup failed — aborting upgrade.`** | Meist eine volle Festplatte oder ein Postgres, das nicht läuft. Schaffen Sie Platz, prüfen Sie `faden status` und führen Sie das Upgrade erneut aus. Verwenden Sie `--no-backup` nur, wenn Sie eine aktuelle Sicherung haben. |
| **`Backend did not become healthy within 120 s.`** | Die Dienste wurden neu gestartet, aber die Konsolen-API antwortet noch nicht. Lesen Sie `faden logs faden-backend`. Die Wartungsseite wird trotzdem aufgehoben, damit erreichbar ist, was läuft. |
| **`... this release names a build that is on no registry.`** | Ein Runtime-Image für eine Maschinenart ist nicht veröffentlicht. Das Upgrade ist davon nicht betroffen, und laufende Cluster behalten ihre Images. Eine Maschine dieser Art kann das Image weiterhin von einer anderen Maschine ihres Clusters bekommen, die es hat. |
| Die Festplatte ist voll: Postgres, der Log-Speicher und die anderen Speicher starten immer wieder neu | Nichts funktioniert, bis wieder Platz da ist: Entfernen Sie Images, die kein Container verwendet (`docker image prune`), verschieben Sie alte Sicherungen vom Server und führen Sie das Upgrade dann erneut aus. Das Dashboard warnt früher: **Wenig Speicherplatz**. |
