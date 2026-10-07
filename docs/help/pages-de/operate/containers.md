# Container {#containers}

Die Seiten unter **Container** in der Konsole verwalten Docker auf dem Server: die Container, die Fadenstack selbst
betreibt, und alle, die Sie daneben hinzufügen, z. B. einen MCP-Server oder einen kleinen Dienst, den Ihre Modelle
aufrufen. Die GPU-Maschinen verwalten sie nicht; was dort läuft, verwalten Sie über [Cluster](clusters.md) und
Bereitstellungen.

## Die Liste {#the-list}

Öffnen Sie **Container → Container**. Die Liste zeigt jeden Container auf dem Server, auch die von Fadenstack.
Vier Kacheln über der Liste zählen sie, und jede ist zugleich ein Filter (wählen Sie sie erneut, um wieder alle
aufzulisten):

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

![Die Container-Liste: die Kacheln Läuft, Läuft nicht, Fehlerhaft und Startet neu über den Containern von Fadenstack mit ihrer Klasse](../images/operate/containers.png)

Die Liste **Handlungsbedarf** im Dashboard meldet Container, die immer wieder neu starten.

### Klassen {#classes}

Die Klasse eines Containers legt fest, was die Konsole mit ihm zulässt, gleich für wen:

| Klasse | Für | Ansehen, Logs | Starten, neu starten | Stoppen | Pausieren, Exec, Löschen |
|---|---|---|---|---|---|
| `SYSTEM_CORE` | Postgres, Redis, der Proxy, RabbitMQ, die Speicher für Logs und Metriken | ja | ja | nein | nein |
| `SYSTEM_AUX` | Die eigenen Dienste und Module von Fadenstack | ja | ja | ja | nein |
| `MCP` | MCP-Server | ja | ja | ja | nein |
| `TENANT_APP` | Container, die Sie betreiben | ja | ja | ja | ja |
| `UNTRUSTED` | Alles, was nicht klassifiziert ist | ja | nein | nein | nein |

Ein Container, der anderswo erstellt wurde, wird nach seinem Namen klassifiziert, wenn die Liste ihn zum ersten
Mal sieht: Ein Name, der den Namen eines Fadenstack-Dienstes oder der Infrastruktur oben enthält (`redis`,
`postgres` usw.), macht ihn zum Systemcontainer, ein Name, der `mcp-` enthält, zu `MCP`, alles andere zu
`UNTRUSTED`. Die Klasse eines Containers ändern Sie in seiner Zeile;
`SYSTEM_CORE` bleibt gesperrt.

Der **Root-Modus** hebt die Grenzen der Klassen für den Administrator auf, der ihn aktiviert, für Wartungsarbeiten
am System. Wählen Sie **Root-Modus aktivieren** und geben Sie eine **Begründung** (mindestens 10 Zeichen) und eine
**Dauer** (60 bis 3600 Sekunden) an. Jede Aktion im Root-Modus wird mit hoher Schwere protokolliert. Ein Container,
dessen Richtlinie **locked** ist, lässt sich auch dann nicht stoppen, pausieren, bearbeiten oder löschen.

## Ein Image holen {#get-an-image}

Ein Container startet aus einem Image auf dem Server; beim Erstellen eines Containers wird das Image nicht
heruntergeladen.

1. Öffnen Sie **Container → Images**.
2. Geben Sie Name und Tag des Images ein und wählen Sie **Image ziehen**. Während des Downloads wird der
   Fortschritt angezeigt.

![Die Seite Images: Image und Tag mit Image ziehen, Dangling-Images bereinigen mit Testlauf und die Images auf dem Server](../images/operate/images.png)

**Dangling-Images bereinigen** entfernt Images, auf die kein Tag verweist. Dafür ist der Root-Modus nötig; machen
Sie zuerst einen **Testlauf**.

## Einen Container erstellen {#create-a-container}

1. Öffnen Sie **Container → Container** und wählen Sie **Erstellen**.
2. Füllen Sie **Image** aus (die Vorschläge kommen aus den Images auf dem Server), dazu **Containername** und bei
   Bedarf **Befehl überschreiben**, geschrieben wie in einer Shell.
3. Wählen Sie die **Klasse**: `TENANT_APP` für einen eigenen Container, `MCP` für einen MCP-Server. Setzen Sie
   **Richtlinie** und **Bereich**, wenn Sie diese verwenden.
4. Fügen Sie **Port-Bindings** nur hinzu, wenn etwas außerhalb des Servers den Container erreichen muss. Die
   Dienste von Fadenstack erreichen ihn auch ohne über sein Netzwerk unter seinem Namen. Ein Host-Port muss auf
   dem Server frei sein.
5. Fügen Sie nach Bedarf **Umgebungsvariablen** und **Volume-Mounts** hinzu.
6. Wählen Sie unter **Netzwerke** `fadenstack_default`, damit das Gateway, die Konsolen-API und der MCP-Dienst den
   Container unter seinem Namen erreichen (z. B. `http://my-service:8080`). Ohne Netzwerk kommt er in die
   Standard-Bridge von Docker.
7. Schalten Sie **Sofort nach Erstellung starten** ein und wählen Sie **Erstellen und starten**, oder lassen Sie es
   aus und wählen Sie **Container erstellen**, um den Container gestoppt anzulegen.

!!! note
    Umgebungsvariablen sind für jeden sichtbar, der den Container sehen kann. Legen Sie dort keine Geheimnisse ab,
    die diese Personen nicht sehen sollen.

## Mit einem Container arbeiten {#work-with-a-container}

Seine Seite hat drei Tabs:

- **Übersicht**: Status, Image, Netzwerke.
- **Logs**: **Letzte 300 Zeilen**, zum Aktualisieren oder Herunterladen.
- **Exec**: bereitet eine Shell-Sitzung im Container vor und zeigt ihre ID. Ein Terminal gibt es auf der Seite
  nicht, und noch verbindet sich nichts in der Konsole mit der Sitzung. Exec braucht die Klasse `TENANT_APP` oder den Root-Modus.

Stoppen Sie einen Container, bevor Sie ihn löschen: Ein laufender Container wird nicht gelöscht. Ein Prozess, der
das Stoppsignal ignoriert, wird nach zehn Sekunden zwangsweise beendet und erscheint als `Exited (137)`. Vor dem
Löschen fragt die Konsole nach einer Bestätigung; das Image bleibt unter **Images** erhalten.

## Einen Container bearbeiten {#edit-a-container}

**Bearbeiten** auf der Seite des Containers öffnet das Formular mit den Einstellungen, mit denen der Container
erstellt wurde. Docker kann diese Einstellungen an einem bestehenden Container nicht ändern; deshalb ersetzt
**Speichern und ersetzen** ihn, nachdem Sie bestätigt haben:

- ein neuer Container wird mit den neuen Einstellungen erstellt, in denselben Netzwerken und unter demselben Namen;
- was das Formular nicht zeigt, bleibt erhalten: GPU-Geräte, Neustartrichtlinie, Labels, Health-Check;
- der alte Container wird entfernt, sobald der neue läuft. Der Container erhält eine neue ID, und Dateien, die er
  außerhalb seiner Volumes geschrieben hat, gehen verloren;
- kann der neue nicht erstellt oder gestartet werden (ein Image, das nicht auf dem Server liegt, ein belegter
  Host-Port), wird der alte wiederhergestellt, wie er war, und die Seite nennt den Grund.

Auch die eigenen Container von Fadenstack lassen sich bearbeiten, für einen Test oder eine schnelle Korrektur. Das
nächste `faden deploy` oder `faden upgrade` erstellt sie aber aus der Compose-Datei und `.env` neu, ohne Ihre
Änderungen; das Formular weist darauf hin. Den Container der Konsolen-API selbst können Sie nicht aus der Konsole
bearbeiten: Ändern Sie seine Einstellungen in `~/fadenstack/.env` und führen Sie `faden upgrade` (oder `faden up`)
aus.

![Das Bearbeitungsformular eines der eigenen Container von Fadenstack mit dem Hinweis, dass das nächste faden deploy oder upgrade ihn ohne diese Änderungen neu erstellt](../images/operate/container-edit-notice.png)

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
