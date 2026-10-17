# Container {#containers}

Die Seiten unter **Container** in der Konsole verwalten Docker auf dem Server: die Container, die Fadenstack selbst
betreibt, und alle, die Sie daneben hinzufügen, z. B. einen MCP-Server oder einen kleinen Dienst, den Ihre Modelle
aufrufen. Die GPU-Maschinen verwalten sie nicht; was dort läuft, verwalten Sie über [Cluster](clusters.md) und
Bereitstellungen.

![Die Container-Liste: die Kacheln Läuft, Läuft nicht, Fehlerhaft und Startet neu über den Containern von Fadenstack mit ihrer Klasse](../images/operate/containers.png)

## Die Liste {#the-list}

Öffnen Sie **Container → Container**. Die Liste zeigt jeden Container auf dem Server, auch die von Fadenstack.
Die vier Kacheln zählen sie, und jede ist zugleich ein Filter (wählen Sie sie erneut, um wieder alle aufzulisten).

Die Liste **Handlungsbedarf** im Dashboard meldet Container, die immer wieder neu starten.

??? info "Was die Kacheln und die Spalten zeigen"
    | Kachel | Zählt |
    |---|---|
    | **Läuft** | Laufende Container, von allen aufgelisteten |
    | **Läuft nicht** | Beendet, erstellt oder pausiert |
    | **Fehlerhaft** | Laufen, bestehen aber ihren Health-Check nicht |
    | **Startet neu** | Starten neu, oft in einer Absturzschleife |

    | Spalte | Was sie zeigt |
    |---|---|
    | Name | Name und Kurz-ID des Containers |
    | **Image** | Was er ausführt |
    | **Status** | Läuft, beendet oder pausiert, und seit wann |
    | **Klasse** | Was die Konsole mit ihm tun darf (siehe unten) |
    | **Endpunkt** | Der Host-Port, auf dem er veröffentlicht ist, falls vorhanden |
    | **Bereich** | Das Team oder die Einheit, der er gehört |
    | Aktionen | Starten, stoppen, pausieren, neu starten, löschen, Details: nur, was seine Klasse erlaubt |

### Klassen {#classes}

Die Klasse eines Containers legt fest, was die Konsole mit ihm zulässt, gleich für wen:

| Klasse | Für | Ansehen, Logs | Starten, neu starten | Stoppen | Pausieren, Löschen |
|---|---|---|---|---|---|
| `SYSTEM_CORE` | Postgres, Redis, der Proxy, RabbitMQ, die Speicher für Logs und Metriken | ja | ja | nein | nein |
| `SYSTEM_AUX` | Die eigenen Dienste und Module von Fadenstack | ja | ja | ja | nein |
| `MCP` | MCP-Server | ja | ja | ja | nein |
| `TENANT_APP` | Container, die Sie betreiben | ja | ja | ja | ja |
| `UNTRUSTED` | Alles, was nicht klassifiziert ist | ja | nein | nein | nein |

Die Klasse eines Containers ändern Sie in seiner Zeile; `SYSTEM_CORE` bleibt gesperrt.

??? info "Wie ein anderswo erstellter Container seine Klasse erhält"
    Ein Container, der anderswo erstellt wurde, wird nach seinem Namen klassifiziert, wenn die Liste ihn zum ersten
    Mal sieht: Ein Name, der den Namen eines Fadenstack-Dienstes oder der Infrastruktur oben enthält (`redis`,
    `postgres` usw.), macht ihn zum Systemcontainer, ein Name, der `mcp-` enthält, zu `MCP`, alles andere zu
    `UNTRUSTED`.

Der **Root-Modus** hebt die Grenzen der Klassen für den Administrator auf, der ihn aktiviert, für Wartungsarbeiten
am System. Wählen Sie **Root-Modus aktivieren** und geben Sie eine **Begründung** und eine **Dauer** an. Jede Aktion
im Root-Modus wird mit hoher Schwere protokolliert.

??? info "Mehr zum Root-Modus"
    Die **Begründung** braucht mindestens 10 Zeichen; die **Dauer** beträgt 60 bis 3600 Sekunden. Ein Container,
    dessen Richtlinie **locked** ist, lässt sich auch im Root-Modus nicht stoppen, pausieren, bearbeiten oder löschen.

## Ein Image holen {#get-an-image}

![Die Seite Images: Image und Tag mit Image ziehen, Dangling-Images bereinigen mit Testlauf und die Images auf dem Server](../images/operate/images.png)

Ein Container startet aus einem Image auf dem Server; beim Erstellen eines Containers wird das Image nicht
heruntergeladen.

1. Öffnen Sie **Container → Images**.
2. Geben Sie Name und Tag des Images ein und wählen Sie **Image ziehen**. Während des Downloads wird der
   Fortschritt angezeigt.

**Dangling-Images bereinigen** entfernt Images, auf die kein Tag verweist. Dafür ist der Root-Modus nötig; machen
Sie zuerst einen **Testlauf**.

## Einen Container erstellen {#create-a-container}

![Container erstellen: Image, Containername und Befehl überschreiben; Klasse, Richtlinie und Bereich; Port-Bindings, Umgebungsvariablen und Volume-Mounts; Sofort nach Erstellung starten und Netzwerke; und Container erstellen](../images/operate/container-new.png)

1. Öffnen Sie **Container → Container** und wählen Sie **Erstellen**.
2. Füllen Sie **Image** und **Containername** aus.
3. Wählen Sie die **Klasse**: `TENANT_APP` für einen eigenen Container, `MCP` für einen MCP-Server.
4. Fügen Sie **Port-Bindings** nur hinzu, wenn etwas außerhalb des Servers den Container erreichen muss.
5. Fügen Sie nach Bedarf **Umgebungsvariablen** und **Volume-Mounts** hinzu.
6. Wählen Sie unter **Netzwerke** `fadenstack_default`, damit das Gateway, die Konsolen-API und der MCP-Dienst den
   Container unter seinem Namen erreichen (z. B. `http://my-service:8080`).
7. Schalten Sie **Sofort nach Erstellung starten** ein und wählen Sie **Erstellen und starten**, oder lassen Sie es
   aus und wählen Sie **Container erstellen**, um den Container gestoppt anzulegen.

??? info "Mehr zu den Feldern"
    - **Image**: Die Vorschläge kommen aus den Images auf dem Server.
    - **Befehl überschreiben** ist optional, geschrieben wie in einer Shell.
    - **Richtlinie** und **Bereich**: Setzen Sie sie, wenn Sie diese verwenden.
    - **Port-Bindings**: Die Dienste von Fadenstack erreichen den Container auch ohne über sein Netzwerk unter seinem
      Namen. Ein Host-Port muss auf dem Server frei sein.
    - **Netzwerke**: Ohne Netzwerk kommt der Container in die Standard-Bridge von Docker.

!!! note
    Die Konsole verbirgt Werte, die geheim aussehen: Variablen, deren Name auf Passwort, Schlüssel, Token oder
    Geheimnis lautet, das Passwort in einer URL und den Wert nach einem Schalter wie `--requirepass`. Das
    Bearbeitungsformular zeigt sie als Punkte; unverändert bleiben sie erhalten. Jeder Blick auf die Details oder
    Einstellungen eines Systemcontainers wird im Audit-Protokoll festgehalten. Werte, die die Konsole nicht als
    geheim erkennt, werden angezeigt; benennen Sie Geheimnisse daher als solche.

Container erstellen, bearbeiten und neu klassifizieren kann nur ein Superuser. Ein Container, der Root-Rechte auf dem
Server hätte, braucht den **Root-Modus**: einer, der einen Ordner oder eine Datei des Servers einbindet (einen
Host-Pfad wie `/srv/data`, `./config` oder `C:\data`; darunter auch den Docker-Socket, `/` und Systemordner wie
`/etc`), oder der das Netzwerk `host` oder das Netzwerk eines anderen Containers nutzt. Ohne Root-Modus lehnt die
Konsole ihn ab und sagt, warum. Geben Sie unter **Volume-Mounts** statt eines Host-Pfads einen Namen an (`my-data`,
nicht `/data/app`): Docker legt dann ein benanntes Volume für den Container an, und das braucht keinen Root-Modus.
Ohne ihn darf nur die Zeitzone schreibgeschützt eingebunden werden (`/etc/localtime`, `/etc/timezone`,
`/usr/share/zoneinfo`, mit `:ro`).

??? info "Mehr dazu, was den Root-Modus braucht"
    - **Bearbeiten**: Der neue Container behält, was das Formular nicht zeigt. Deshalb braucht eine Bearbeitung den
      Root-Modus auch, wenn der Container privilegiert läuft, die Prozesse oder den Speicher des Servers teilt,
      dessen Geräte nutzt (GPUs ausgenommen) oder die Volumes eines anderen Containers übernimmt. Die meisten
      eigenen Container von Fadenstack binden Dateien der Installation ein; sie zu bearbeiten, braucht daher den
      Root-Modus.
    - **Klasse**: Die Klasse eines Containers mit einem der genannten Punkte oder eines `SYSTEM_CORE`- oder
      `SYSTEM_AUX`-Containers zu ändern, braucht den Root-Modus; ebenso das Entsperren eines gesperrten
      Systemcontainers.
    - Starten, Stoppen und Neustarten brauchen keinen Root-Modus: Sie führen im Container nichts Neues aus.

## Mit einem Container arbeiten {#work-with-a-container}

![Die Seite eines Containers: Name, Klasse und ID, die Tabs Übersicht und Logs, Status und Netzwerke, und Bearbeiten](../images/operate/container.png)

Seine Seite hat zwei Tabs:

| Tab | Was er tut |
|---|---|
| **Übersicht** | Status, Image, Netzwerke |
| **Logs** | **Letzte 300 Zeilen**, zum Aktualisieren oder Herunterladen |

Stoppen Sie einen Container, bevor Sie ihn löschen: Ein laufender Container wird nicht gelöscht. Vor dem Löschen
fragt die Konsole nach einer Bestätigung; das Image bleibt unter **Images** erhalten.

??? info "Wenn ein Container nicht stoppt"
    Ein Prozess, der das Stoppsignal ignoriert, wird nach zehn Sekunden zwangsweise beendet und erscheint als
    `Exited (137)`.

## Einen Container bearbeiten {#edit-a-container}

![faden-gateway bearbeiten: der Hinweis zur Compose-Datei über dem Formular mit Image, Containername, Befehl überschreiben, Port-Bindings, Umgebungsvariablen, Klasse, Richtlinie, Bereich, Nach dem Speichern starten und Netzwerke](../images/operate/container-edit.png)

1. Wählen Sie auf der Seite des Containers **Bearbeiten**. Das Formular öffnet sich mit den Einstellungen, mit denen
   der Container erstellt wurde.
2. Ändern Sie, was nötig ist, und wählen Sie **Speichern und ersetzen**.
3. Bestätigen Sie.

Docker kann diese Einstellungen an einem bestehenden Container nicht ändern; deshalb ersetzt **Speichern und
ersetzen** ihn: Ein neuer Container wird mit den neuen Einstellungen erstellt, in denselben Netzwerken und unter
demselben Namen, und der alte Container wird entfernt, sobald der neue läuft.

!!! warning
    Der Container erhält eine neue ID, und Dateien, die er außerhalb seiner Volumes geschrieben hat, gehen verloren.

??? info "Mehr zum Ersetzen eines Containers"
    - Was das Formular nicht zeigt, bleibt erhalten: GPU-Geräte, Neustartrichtlinie, Labels, Health-Check.
    - Kann der neue nicht erstellt oder gestartet werden (ein Image, das nicht auf dem Server liegt, ein belegter
      Host-Port), wird der alte wiederhergestellt, wie er war, und die Seite nennt den Grund.

![Das Bearbeitungsformular eines der eigenen Container von Fadenstack mit dem Hinweis, dass das nächste faden deploy oder upgrade ihn ohne diese Änderungen neu erstellt](../images/operate/container-edit-notice.png)

Auch die eigenen Container von Fadenstack lassen sich bearbeiten, für einen Test oder eine schnelle Korrektur. Das
nächste `faden deploy` oder `faden upgrade` erstellt sie aber aus der Compose-Datei und `.env` neu, ohne Ihre
Änderungen; das Formular weist darauf hin. Den Container der Konsolen-API selbst können Sie nicht aus der Konsole
bearbeiten: Ändern Sie seine Einstellungen in `~/fadenstack/.env` und führen Sie `faden upgrade` (oder `faden up`)
aus.

## Netzwerke und Stacks {#networks-and-stacks}

**Container → Netzwerke** listet die Netzwerke von Docker. `fadenstack_default` ist das eigene Netzwerk von
Fadenstack; Systemnetzwerke sind schreibgeschützt. Sie können ein Netzwerk erstellen (wahlweise intern, ohne
Zugang nach außen), eines untersuchen und die von Ihnen erstellten löschen.

**Container → Stacks** bewahrt Docker-Compose-Dateien unter einer ID auf. **Stack deployen** speichert eine; ein
erneutes Speichern unter derselben ID erzeugt eine neue Revision. Jede Revision bleibt erhalten, zwei lassen sich
vergleichen, und Sie können zu einer früheren zurückkehren. Trotz seines Namens startet **Stack deployen** die
Container nicht, die eine Datei beschreibt: Führen Sie sie selbst mit `docker compose` auf dem Server aus.

## Siehe auch {#related}

- [MCP-Server](../administer/mcp-servers.md): Ein MCP-Server kann hier als Container laufen und unter seinem Namen
  im Netzwerk registriert werden.
