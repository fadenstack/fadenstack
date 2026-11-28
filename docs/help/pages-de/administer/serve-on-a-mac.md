# Modelle auf einem Mac bereitstellen (experimentell)

!!! note "Experimentell"
    Das Bereitstellen auf einem Mac ist neu in dieser Version. Es funktioniert, aber rechnen Sie mit Änderungen. Melden Sie Probleme.

Ein Mac mit Apple-Silicon kann ein Modell allein bereitstellen: ein Mac, ein Modell zur Zeit, kein Cluster. Diese
Seite beschreibt die Einrichtung in der Konsole, was die Statuskarte des Macs zeigt, wie viel Speicher ein Modell
nutzen darf und was ein Mac nicht kann.

## Voraussetzungen

- Auf dem Mac ist der Fadenstack-Agent installiert, und er erscheint unter **KI-Infrastruktur → Maschinen**
  (siehe [Maschinen verbinden](../operate/machines.md)). Der Agent muss mindestens Version 0.3.0 haben.
- Er hat genug Speicher für das Modell. Ein Mac gibt der Engine des Modells etwa vier Fünftel seines Speichers
  (der Rest bleibt macOS), abzüglich einer Reserve: 3 GiB bei 36 GiB, 4 GiB bei 64 GiB, 6 GiB bei 128 GiB.
  Ein Mac braucht mindestens 24 GB Speicher, um ein Modell bereitzustellen.

## Den Mac einrichten

1. Öffnen Sie den Mac unter **KI-Infrastruktur → Maschinen**.
2. Wählen Sie **Modelle bereitstellen**. Dadurch entsteht eine Umgebung nur für diesen Mac, die sich
   öffnet. Wiederholen Sie den Schritt, öffnet sich dieselbe Umgebung.
3. Stellen Sie ein Modell wie üblich bereit (siehe [Bereitstellungen](deployments.md)). Die erste Bereitstellung lädt
   die Laufzeit auf den Mac, was einige Minuten dauert.

Das Laden eines Modells dauert länger als der Start eines Containers: ein kleines Modell braucht ein bis zwei Minuten,
ein großes mehrere. Die Bereitstellung zeigt **Modell wird geladen**, bis sie bereit ist; wird ein Modell nicht
rechtzeitig bereit, gilt die Bereitstellung als fehlgeschlagen.

## Die Statuskarte des Macs

| Zeile | Bedeutung | Was zu tun ist |
|---|---|---|
| Native Laufzeit | Installiert und bereit, oder wird mit dem ersten Modell geladen. | Nichts. |
| Sandbox | An: Die Engine kann die eigenen Dateien des Agenten nicht lesen. | An lassen. Nur zum Testen ausschalten. |
| Anwendungs-Firewall | Wenn an, kann sie das Gateway am Erreichen des Modell-Ports hindern. | Unter Systemeinstellungen, Netzwerk, Firewall eingehende Verbindungen für `faden-agent` erlauben. |
| FileVault | Wenn an, wartet der Mac nach einem Stromausfall auf eine Anmeldung. | Mit `sudo fdesetup authrestart` neu starten, damit er unbeaufsichtigt zurückkommt, oder FileVault ausgeschaltet lassen. |

## Speicher {#memory-share}

Wählen Sie den Mac im Bereitstellungsdialog, zeigt er, was die GPU nutzen kann, was dem System bleibt, die Gewichte
des Modells, den Platz für Unterhaltungen und den Speicheranteil des Modells. Eine als *geschätzt* markierte Zahl
stammt aus dem Gesamtspeicher des Macs, bis die Laufzeit den echten Wert gemeldet hat.

Ein Modell, das mehr braucht, als der Mac geben kann, wird abgelehnt, mit dem nötigen und dem verfügbaren Speicher.
Ein kleinerer Kontext oder weniger parallele Unterhaltungen können es passend machen; der Dialog schlägt beides vor.

## Ein Mac mini als dedizierter Knoten {#a-mac-mini-as-a-dedicated-node}

Der Fadenstack-Server (Konsole, Gateway, Überwachung) läuft auf einer anderen Maschine. Der Mac tritt ihm über das
Netzwerk als Knoten bei, und das Gateway erreicht das Modell des Macs über dessen Adresse. Ein Mac mini oder ein Mac
Studio ist der typische Knoten: Er hat weder Akku noch Deckel und läuft nach der Einrichtung ohne Bildschirm.

Richten Sie den Mac einmal mit Bildschirm und Tastatur ein, verbinden Sie ihn mit dem Server, schalten Sie
„Entfernte Anmeldung“ ein (siehe unten), und danach kann er ohne Bildschirm laufen.

### Speicher

Ein Mac braucht mindestens 24 GB vereinheitlichten Speicher, um ein Modell bereitzustellen. Der Basis-Mac mini hat
16 GB und kann das nicht; die Konsole sagt dann, dass er mindestens 24 GB Speicher braucht. Wählen Sie beim Kauf 24 GB
oder mehr. Wie viel davon das Modell nutzen darf, steht oben unter [Speicher](#memory-share).

### FileVault und unbeaufsichtigte Neustarts {#filevault-and-unattended-restarts}

Ist FileVault an, bleibt ein Mac, der nach einem Stromausfall oder einem automatischen macOS-Update neu startet, am
Entsperrbildschirm stehen. Das Volume ist gesperrt, der Agent startet nicht, und die Maschine bleibt offline, bis
jemand sie entsperrt. Sie haben drei Möglichkeiten:

| Möglichkeit | Vorgehen | Nachteil |
|---|---|---|
| Per SSH entsperren | **Entfernte Anmeldung** einschalten (Systemeinstellungen, Allgemein, Teilen). Nach einem Neustart verbinden Sie sich über das Netzwerk und geben das Passwort ein; danach startet der Mac fertig. Setzt einen Mac mit Apple-Silicon und macOS 26 oder neuer sowie eine Netzwerkverbindung voraus. | Jemand muss das Passwort haben und erreichbar sein. Entsperrt wird mit Passwort: Schlüssel funktionieren in dieser Phase nicht. Tastatur und Bildschirm sind nicht nötig. |
| `sudo fdesetup authrestart` | Für Neustarts, die Sie selbst auslösen. Der Mac startet neu und überspringt die Entsperrung einmal. | Gilt nur für geplante Neustarts, nicht für Stromausfall oder Absturz. Der Schutz durch FileVault ist für diesen einen Neustart verringert. Es funktioniert nicht auf jedem Mac. |
| FileVault aus | Der Mac startet nach jedem Neustart direkt in den Agenten. | Das Volume ist nicht mit Ihrem Passwort verschlüsselt. Wer den Mac mitnimmt, kann es lesen, auch die Zugangsdaten des Agenten und die geladenen Modelle. Wählen Sie das nur, wenn der Diebstahl des Macs nicht zu Ihrem Bedrohungsmodell gehört. |

Fadenstack schaltet FileVault nie für Sie aus. `faden-agent status` gibt eine Zeile aus, wenn FileVault an und die
Entfernte Anmeldung aus ist, denn dann braucht ein Stromausfall jemanden am Mac. Eine kleine USV verkürzt die
Zeit, in der ein Stromausfall wirkt.

!!! note
    Eine automatische macOS-Installation kann den Mac neu starten. Ist FileVault an und können Sie nicht entsperren,
    schalten Sie **macOS-Updates installieren** unter Systemeinstellungen, Allgemein, Softwareupdate aus, oder
    installieren Sie Updates, wenn Sie den Mac entsperren können.

### Energieeinstellungen

Prüfen Sie diese Einstellungen in den Systemeinstellungen, bevor der Mac ohne Bildschirm läuft:

- **Energie, Hochfahren, wenn Strom angeschlossen wird: Immer.** In älteren macOS-Versionen heißt die Option
  **Nach einem Stromausfall automatisch neu starten**. Ohne sie bleibt ein Desktop-Mac nach einem Stromausfall aus.
- **Ruhezustand für Netzwerkzugriff beenden: an.**
- **Allgemein, Teilen, Entfernte Anmeldung: an**, solange Sie noch am Mac sind.
- **Softwareupdate, macOS-Updates installieren:** Entscheiden Sie das bewusst (siehe Hinweis oben).
- Ruhezustand bei Inaktivität: nichts zu tun. Solange er läuft, hält der Agent den Mac wach, und `faden-agent
  status` zeigt es an. Der Bildschirm darf weiterhin ausgehen.
- Der **Modus „Hohe Leistung“** (Energie, Energiemodus, am Netzteil) gibt es nur bei manchen Modellen, etwa einem
  Mac mini mit Pro-Chip. Die Lüfter laufen dann bei langer, hoher Last schneller und werden lauter. Fadenstack
  ändert ihn nicht; die Entscheidung liegt bei Ihnen.

### Der Modellspeicher

Modelle liegen auf der internen SSD des Macs, in `/var/db/fadenstack/agent/models`. Prüfen Sie zuerst den freien
Platz: Ein Basis-Mac mini hat eine SSD mit 256 GB, und ein großes Modell kann mehrere zehn GB belegen.

Der Agent nutzt einen anderen Ordner, gesetzt mit `FADEN_NODE_AGENT_MODEL_STORE` in `/Library/Fadenstack/agent.env`,
nur, wenn es ein absoluter Pfad zu einem vorhandenen Ordner ist, der `_fadenagent` gehört und den Modus 0700 hat.
Ordner im Home-Ordner, auf geschützten Systempfaden, in den eigenen Ordnern des Agenten sowie symbolische Links
werden abgelehnt. Besteht ein Speicher die Prüfung nicht, wird er nicht benutzt: Der Start gibt eine Warnung aus, der
Agent nimmt die interne SSD, und Modelle im alten Speicher werden dort nicht gefunden.

macOS kann bei einem externen Laufwerk die Besitzer ignorieren, sodass jede Datei dem aktuellen Benutzer zu gehören
scheint. Dann wird der Speicher abgelehnt. Für eine externe SSD kann ein Administrator Folgendes tun, mit Sorgfalt,
denn es ändert, wie das ganze Laufwerk behandelt wird:

1. Formatieren Sie das Laufwerk als APFS, falls es das nicht ist. Das löscht es.
2. Wählen Sie das Laufwerk im Finder, dann **Ablage, Informationen**, öffnen Sie **Teilen & Zugriffsrechte** und
   achten Sie darauf, dass **Eigentümer auf diesem Volume ignorieren** nicht aktiviert ist.
3. Legen Sie im Terminal den Ordner an und übergeben Sie ihn dem Agenten:
   `sudo mkdir /Volumes/Models/faden && sudo chown _fadenagent /Volumes/Models/faden && sudo chmod 700 /Volumes/Models/faden`
   (mit dem Namen Ihres Volumes).
4. Tragen Sie den Pfad in `agent.env` als `FADEN_NODE_AGENT_MODEL_STORE` ein, starten Sie den Agenten mit `sudo
   faden-agent start` neu und lesen Sie, was er ausgibt.

Das ist nicht in jeder Konfiguration getestet. Prüfen Sie insbesondere nach einem Neustart, ob das Laufwerk
eingehängt ist, wenn der Agent startet; fehlt der Ordner, nimmt der Agent die interne SSD. Im Zweifel nutzen Sie
die interne SSD.

### Das Netzwerk

- Bevorzugen Sie **Ethernet**. Ein Mac im WLAN kann starten, bevor das Netzwerk bereit ist.
- Der Mac meldet dem Server seine Adresse, wenn der Agent startet, und die Adresse eines Modells steht fest, wenn es
  bereitgestellt wird. Richten Sie für den Mac eine **DHCP-Reservierung** im Router ein, damit sich die Adresse nicht
  ändert, oder legen Sie die Adresse mit `FADEN_NODE_AGENT_ADVERTISE_HOST` in `agent.env` selbst fest. Tun Sie das
  auch, wenn der Mac mehrere Netzwerke hat und der Server ihn über ein bestimmtes erreicht.
- Die **Firewall** von macOS kann den Server am Erreichen des Macs hindern. `faden-agent status` sagt es, wenn der
  Agent nicht erlaubt ist, und gibt den genauen Befehl aus, den ein Administrator einmal ausführt. Ein neuer Build des
  Agenten hat einen neuen Pfad und braucht den Befehl womöglich erneut.
- **Ports:** Erlauben Sie die Modell-Ports **8100-8999** vom Server aus und **9468** für Metriken. Metriken sind
  unverschlüsseltes HTTP ohne Anmeldung, wie unter Linux; machen Sie 9468 daher nur aus dem Netz des Servers erreichbar.

Beispiel für einen Mac, den der Server unter `192.0.2.10` erreicht:

```
FADEN_NODE_AGENT_ADVERTISE_HOST=192.0.2.10
```

### Mehrere Macs

Ein Modell kann noch nicht mehrere Macs umfassen. Ein Modell läuft auf einem Mac. Für mehr Macs stellen Sie auf
jedem ein Modell bereit.

## Was ein Mac nicht kann

- Ein Modell zur Zeit, auf einem Mac. Löschen Sie die laufende Bereitstellung, bevor Sie ein anderes bereitstellen.
- Keine Skalierung, keine Kopien auf anderen Maschinen, kein Entleeren und kein Netzwerkplan: das sind Funktionen von Clustern.
- GPTQ-Modelle werden abgelehnt; AWQ- und MLX-Modelle funktionieren. Ein quantisierter KV-Cache, ein Limit für Denk-Tokens,
  Prefix-Caching, spekulatives Dekodieren und Modelle, die Code aus ihrem Repository ausführen, werden nicht angeboten.
- Angeboten werden nur der Tool-Call-Parser `hermes` und der Reasoning-Parser `qwen3`. Weitere folgen nach ihrer Prüfung.
- Ein Mac kann nicht gleichzeitig Mitglied eines Clusters sein und allein bereitstellen.
