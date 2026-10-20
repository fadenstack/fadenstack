# Upgrade {#upgrading}

So bringen Sie den Server auf ein neues Release, danach die Agenten auf den GPU-Maschinen, und so stellen Sie
einen Cluster auf eine neuere Runtime um. Jeder dieser Schritte ist eigenständig und wird von Ihnen gestartet;
nichts aktualisiert sich von selbst.

| Was | Wie | Was es unterbricht |
|---|---|---|
| Der Server | `faden upgrade` | Die Konsole und das Gateway, für die Minuten, in denen die Dienste neu starten. Die Modelle laufen weiter |
| Der Agent einer Maschine | Die Installationszeile auf der Maschine erneut ausführen | Nichts: Der Agent startet neu, die Modelle laufen weiter |
| Die Runtime eines Clusters | **Laufzeit** auf der Seite des Clusters | Jedes Modell auf diesem Cluster, solange es neu lädt |

## Wie Sie von einem neuen Release erfahren {#how-you-hear-about-a-new-release}

Einmal am Tag liest der Server die öffentliche Liste der Fadenstack-Releases,
`https://fadenstack.com/updates/v1/index.json`. Über Ihren Server wird dabei nichts gesendet: Die Liste wird wie
jede Datei abgerufen und auf dem Server verglichen. Ist ein neueres Release erschienen, sehen es die
Administratoren:

| Wo | Was |
|---|---|
| **Updates** in der Konsole | Das Release, mit dem dieser Server läuft, das neueste und was es behebt, die Befehle für diese Installation und was jeder Teil ausführt: die Dienste, der Agent jeder Maschine, die Runtime jedes Clusters |
| **Handlungsbedarf** im Dashboard | Ein Eintrag für jedes neuere Release; ein Release, das Sicherheitsprobleme behebt, als Warnung |
| Ein Hinweisbalken auf den Seiten der Konsole | Für ein Release, das Sicherheitsprobleme behebt, und wenn das Release dieses Servers keine Korrekturen mehr bekommt |
| E-Mail | Eine Nachricht an die Administratoren für ein Release, das Sicherheitsprobleme behebt, wenn E-Mail eingerichtet ist |
| Der Befehl `faden` | Eine Zeile nach einem Befehl, höchstens einmal am Tag: `Fadenstack <new> is available` |

Nichts wird von selbst installiert: Sie führen die Befehle aus, wie unten beschrieben.

![Der Hinweisbalken auf den Seiten der Konsole: Eine neuere Version behebt Sicherheitsprobleme, mit Zu den Updates](../images/operate/update-banner.png)

### Wenn ein Release erschienen ist {#when-a-release-is-out}

1. Ein Hinweisbalken auf den Seiten der Konsole oder ein Eintrag unter **Handlungsbedarf** im Dashboard meldet,
   dass ein Release erschienen ist. Wählen Sie **Zu den Updates** oder den Eintrag.

    ![Handlungsbedarf im Dashboard mit dem Eintrag Sicherheitsupdate: Fadenstack 0.4.2](../images/operate/update-attention.png)

2. **Updates** zeigt das Release, mit dem dieser Server läuft, das neueste, wie dringend es ist (**Update
   verfügbar**, **Sicherheitsupdate**, **Kritisches Update**) und was es behebt. **Versionshinweise** öffnet das
   Release auf [fadenstack.com/releases](https://fadenstack.com/releases/).

    ![Die Seite Updates: Dieser Server läuft mit 0.4.1, 0.4.2 ist ein Sicherheitsupdate, die Befehle zum Aktualisieren und die neueren Versionen](../images/operate/updates.png)

3. Kopieren Sie unter **So aktualisieren Sie** die Befehle und führen Sie sie auf dem Server aus, wie unter
   [Den Server aktualisieren](#upgrade-the-server) beschrieben. Sie passen dazu, wie `faden` auf diesem Server
   installiert wurde, und nennen das genaue Release, wenn sich eines nicht überspringen lässt.

    ![So aktualisieren Sie: pipx upgrade fadenstack, faden upgrade --dry-run, faden upgrade -y](../images/operate/updates-commands.png)

4. Ist das Upgrade fertig, zeigt **Updates** **Aktuell**. Bringt das Release einen neueren Agenten für die
   GPU-Maschinen, sagt **Updates** das, und die Maschinen zeigen **Agent *Version* verfügbar**: siehe
   [Die Agenten der Maschinen aktualisieren](#update-the-machines-agents).

Einen Hinweisbalken für ein Sicherheitsupdate können Sie bis zum nächsten Release ausblenden (**Bis zur nächsten
Version ausblenden**, in diesem Browser). Einen Hinweisbalken für ein kritisches Update oder für ein Release, das
keine Korrekturen mehr bekommt, nicht.

In der Kommandozeile meldet `faden` dasselbe nach einem Befehl, höchstens einmal am Tag:

```text
$ faden status
...
Fadenstack 0.4.2 is available (you have 0.4.1) -- it fixes security problems.
Upgrade: pipx upgrade fadenstack, then faden upgrade
```

??? info "Bestandteile dieser Installation"
    Unter den Releases listet **Updates** auf, was jeder Bestandteil ausführt und was dieses Release erwartet: jeder
    Dienst, der Agent jeder Maschine, die Runtime jedes Clusters und das Release, das das Kommandozeilenwerkzeug
    zuletzt installiert hat.

    | Status | Bedeutung |
    |---|---|
    | **Aktuell** | Er führt aus, was dieses Release erwartet |
    | **Update verfügbar** | Ein neuerer liegt bereit: der Agent einer Maschine oder die Runtime eines Clusters (siehe unten) |
    | **Andere Version** | Ein Dienst läuft mit einem anderen Release als die übrigen. Führen Sie `faden upgrade` erneut aus |
    | **Unbekannt** | Er sagt nicht, mit welchem Release er läuft: ein Dienst, der auf diesem Server aus dem Quellcode gebaut wurde, oder eine Maschine, die sich nie gemeldet hat |
    | **Gestoppt** | Der Dienst läuft nicht |

??? info "Mehr zur Update-Prüfung"
    - **Jetzt prüfen** auf **Updates** liest die Liste sofort; die tägliche Prüfung braucht niemanden.
    - `faden deploy` fragt, ob geprüft werden soll; `--update-check` oder `--no-update-check` beantwortet die
      Frage ohne Rückfrage. Später ändern Sie das in der Konsole unter **Einstellungen**, **Updates** (**Check for
      new releases**) und für die Kommandozeile mit `update_check: false` in `faden.yaml` oder
      `FADEN_NO_UPDATE_CHECK=1`.
    - **Release channel** `preview` meldet auch Release-Kandidaten, um ein Release vor seinem Erscheinen zu
      testen.
    - Die Liste ist signiert. Der Server und `faden` nehmen nur eine vom Fadenstack-Projekt signierte Liste an und
      nie eine ältere als die, die sie schon haben. Eine veränderte Liste kann also weder ein Release verbergen
      noch eines erfinden.
    - `faden version` und `faden doctor` prüfen sofort und sagen, wie Sie aktualisieren.
    - Manche Releases lassen sich nicht überspringen. **Updates** und `faden version` nennen dann die Releases,
      die nacheinander zu installieren sind, und `faden upgrade` lehnt es ab, eines zu überspringen.
    - In einer Kommandozeile, die kein Terminal ist (Skripte, der Zeitgeber der Sicherung), sagt `faden` nichts.

### Ein Server ohne Internetzugang {#a-server-without-internet-access}

- **Hinter einem Proxy:** Tragen Sie unter **Einstellungen**, **Updates** den **Proxy for the release list** ein.
- **Mit einem eigenen Webserver:** Kopieren Sie `index.json` und `index.json.sig` von
  `https://fadenstack.com/updates/v1/` unverändert dorthin und tragen Sie die Adresse der Kopie als **Release list
  address** ein.
- **Mit keinem von beiden:** Laden Sie die beiden Dateien auf einem anderen Rechner herunter und laden Sie sie
  unter **Updates** bei **Kein Internet auf diesem Server?** hoch.

Oder schalten Sie die Prüfung aus: **Updates** zeigt weiterhin, was jeder Teil der Installation ausführt.

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

    **Updates** in der Konsole zeigt diesen Befehl passend dazu, wie das Werkzeug auf diesem Server installiert
    wurde, und die genaue Version, wenn sich ein Release nicht überspringen lässt.

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
       Bemessung der Dienste, die wie bei `faden tune` neu für den Host berechnet wird. Fehlt ein Schlüssel, der
       gespeicherte Daten versiegelt, bekommt er den Wert, mit dem die Installation lief, nie einen neuen, und das
       Upgrade sagt es (siehe [Geheimnisse in der .env](installing.md#secrets-in-env));
    4. lädt die Images des neuen Release herunter;
    5. kopiert hinter der Wartungsseite zuerst die Chat-Anhänge, die eine Installation aus der Zeit vor Release
       0.4.4 im Gateway-Container hält, in ihr Volume und vergleicht jede Datei anhand ihres SHA-256-Hashs. Lässt
       sich eine Datei nicht kopieren oder stimmt sie nicht überein, hält das Upgrade hier an, bevor etwas ersetzt
       wird: Die alten Dienste laufen weiter, und die Dateien bleiben unberührt (siehe die Tabelle am Ende). Ist
       `FADEN_DATA_DIR` gesetzt, kopiert es ebenso die Dateien der Chat- und Wissens-Volumes in die Datenordner und
       prüft sie (siehe [Wo die Firmendaten liegen](installing.md#where-the-company-data-is-kept));
    6. startet die Dienste neu; hier laufen die Datenbankmigrationen, und die beiden
       Postgres-Anmeldungen mit festen Passwörtern, die frühere Releases angelegt haben (`llm_user`,
       `faden_backend`, von nichts genutzt), werden abgeschaltet;
    7. wartet, bis die Konsolen-API antwortet, und hebt dann die Wartungsseite auf;
    8. holt die Runtime-Images, die das neue Release nennt. Nur was sich geändert hat, wird heruntergeladen. Ein
       fehlgeschlagener Download hält nichts auf: Wiederholen Sie ihn mit `faden runtime-images`.

| Option | Was sie tut |
|---|---|
| `--dry-run` | Zeigen, was passieren würde, nichts ändern |
| `-y` | Vor dem Start nicht nachfragen |
| `--backup-dir PATH` | Die Sicherung vor dem Upgrade woanders ablegen |
| `--no-backup` | Die Sicherung überspringen. Nur, wenn Sie eine aktuelle haben |
| `--runtime-images ARCHS` | Welche Runtime-Images geholt werden: `all`, `none`, `x86_64`, `aarch64`. Bleibt gespeichert |

!!! note "Der Chat prüft seit Release 0.4.2 die Rollen"
    Bisher konnte jedes aktive Konto chatten. Jetzt chattet, wer `default_user` hat oder eine Rolle mit den
    Berechtigungen `chat.*`. Nach dem Upgrade chattet ein Konto, dem Sie nur andere Rollen gegeben haben, etwa
    `operator` oder `viewer`, nicht mehr: Kreuzen Sie dafür zusätzlich `default_user` an (siehe
    [Benutzer](../administer/users.md)).

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

## Das PII-Modul aktualisieren {#upgrade-the-pii-module}

Ein eingeschaltetes PII-Modul wird mit `faden upgrade` aktualisiert. Ein ausgeschaltetes bleibt aus.
Bestehende Geheimnisse, das Modellverzeichnis und Richtlinien bleiben erhalten.

1. Prüfen Sie mit `faden upgrade --dry-run` das geplante Upgrade. Dabei werden keine PII-Einstellungen geändert
   oder Modelle heruntergeladen.
2. Führen Sie `faden upgrade` aus. Der PII-Schritt prüft die GPU-Kompatibilität und bietet die Modusauswahl an.
   Vorgabe ist der bisherige Modus. Auf einem CPU-Server mit älterer Installation behält `legacy` die rein
   englische Erkennung bei; `cpu` übernimmt die mehrsprachige CPU-Erkennung.
3. Führen Sie `faden doctor` aus und prüfen Sie das installierte Profil in den
   [Einstellungen für personenbezogene Daten](../administer/policies.md#personal-data).

`faden upgrade -y` fragt nicht nach und behält einen kompatiblen Modus bei. Kann eine installierte GLiNER2-Erkennung
ihre GPU nicht mehr verwenden, läuft sie mit dem bisherigen Profil auf der CPU; die Verarbeitung ist langsamer.
Ein anderes Modell wird nicht stillschweigend eingesetzt. Für einen ausdrücklichen Wechsel:

```bash
faden upgrade --pii-profile cpu
faden upgrade --pii-profile gpu-shared
```

Nach einem Wechsel der Erkennung bleiben die gespeicherten Richtlinien erhalten. Übernehmen Sie das installierte
Profil ausdrücklich in der Konsole. Ohne passendes Profil gilt für geschützte Anfragen die eingestellte
Fehlerbehandlung. Schlägt ein notwendiger Download oder die Prüfung der Modelldateien fehl, stoppt das Upgrade
vor dem Ersetzen der Dienste. Verwenden Sie ohne Download ein geprüftes `--pii-model-dir`.

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
    einer Störung und bei Server-Upgrades, die eine neuere mitbringen. Er behält auch die Einstellungen, mit denen
    er gestartet wurde, etwa wo Modelle und Caches eingebunden sind. Ändert ein Release sie, sagt die Karte
    **Laufzeit** das, und die Modelle laufen weiter; **Übernehmen und neu starten** übernimmt die neuen
    Einstellungen, und nichts startet von selbst neu.

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
| **`<version> cannot upgrade straight to <version>: <version> has to come first.`** | Ein Release dazwischen lässt sich nicht überspringen. Installieren Sie das Werkzeug des genannten Releases, führen Sie `faden upgrade` aus, installieren Sie dann das neuere Werkzeug und führen Sie `faden upgrade` erneut aus. Die Meldung nennt die Befehle. |
| **Updates** meldet, dass die Liste der Releases nicht gelesen werden konnte | Der Server hat kein Internet oder braucht einen Proxy. Siehe [Ein Server ohne Internetzugang](#a-server-without-internet-access). |
| **`Backup failed — aborting upgrade.`** | Meist eine volle Festplatte oder ein Postgres, das nicht läuft. Schaffen Sie Platz, prüfen Sie `faden status` und führen Sie das Upgrade erneut aus. Verwenden Sie `--no-backup` nur, wenn Sie eine aktuelle Sicherung haben. |
| **`Chat attachments could not be copied ...`** oder **`... exist in the data volume with other content ...`** | Eine Installation aus der Zeit vor Release 0.4.4 hält Chat-Anhänge im Gateway-Container, und das Upgrade muss sie in das Volume kopieren, bevor es den Container ersetzt. Das ging nicht, oder das Volume enthält schon eine Datei gleichen Namens mit anderem Inhalt. Es wurde nichts ersetzt und keine Datei überschrieben; die alten Dienste laufen weiter. Schaffen Sie Platz oder beheben Sie die Ursache, die die Meldung nennt (bei einer abweichenden Datei: verschieben Sie die unerwünschte aus dem Volume), und führen Sie `faden upgrade` erneut aus. |
| **`Backend did not become healthy within 120 s.`** | Die Dienste wurden neu gestartet, aber die Konsolen-API antwortet noch nicht. Lesen Sie `faden logs faden-backend`. Die Wartungsseite wird trotzdem aufgehoben, damit erreichbar ist, was läuft. |
| **`... this release names a build that is on no registry.`** | Ein Runtime-Image für eine Maschinenart ist nicht veröffentlicht. Das Upgrade ist davon nicht betroffen, und laufende Cluster behalten ihre Images. Eine Maschine dieser Art kann das Image weiterhin von einer anderen Maschine ihres Clusters bekommen, die es hat. |
| Die Festplatte ist voll: Postgres, der Log-Speicher und die anderen Speicher starten immer wieder neu | Nichts funktioniert, bis wieder Platz da ist: Entfernen Sie Images, die kein Container verwendet (`docker image prune`), verschieben Sie alte Sicherungen vom Server und führen Sie das Upgrade dann erneut aus. Das Dashboard warnt früher: **Wenig Speicherplatz**. |
