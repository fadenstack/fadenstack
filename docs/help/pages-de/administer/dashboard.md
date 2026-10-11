# Das Dashboard {#the-dashboard}

**Dashboard**, der erste Eintrag der Konsole, zeigt das ganze System auf einer Seite: was Ihre Aufmerksamkeit
braucht, wie stark es genutzt wird und wo Anfragen bedient wurden, wie ausgelastet die GPU-Maschinen sind und wie
es um den Server steht, auf dem Fadenstack läuft. Diese Seite erklärt, was jeder Teil zeigt und was Sie dann tun.

![Das Dashboard: oben die Statusanzeige „Kritische Probleme“, „Anpassen“ und der Aktualisierungsring, darunter die Karten „KI-Datenverkehr“, „Handlungsbedarf“, „Kennzahlen“, „Lokale KI-Kapazität“ und „Serverressourcen“](../images/administer/dashboard.png)

## Bevor Sie beginnen {#before-you-start}

- Sie sind **Superuser**: Nur Superuser sehen das Dashboard.

## Die Kopfzeile {#the-header}

| | |
|---|---|
| **Die Statusanzeige** | Fasst den Zustand des Systems zusammen: **System in Ordnung**, **Warnungen: N** oder **Kritische Probleme: N**. Sie zählt, was **Handlungsbedarf** auflistet. |
| **Der Ring** daneben | Zeigt, wann die Zahlen zuletzt aktualisiert wurden. Sie werden alle 30 Sekunden aktualisiert, solange der Browser-Tab geöffnet ist, und pausieren in einem Tab im Hintergrund. Wählen Sie den Ring, um sofort zu aktualisieren. Er wird orange, dann rot, wenn eine Aktualisierung verspätet oder fehlgeschlagen ist; die angezeigten Zahlen sind dann die vorherigen. |
| **Erste Schritte** | Steht über den Karten, bis Sie den Bereich ausblenden (siehe [Fadenstack verwalten](index.md)). |

## Was jede Karte zeigt {#what-each-card-shows}

| Karte | Was sie zeigt | Was Sie damit tun |
|---|---|---|
| **Kennzahlen** | Bis zu sechs Zahlen Ihrer Wahl, jeweils mit ihrem Trend (siehe unten). | Wählen Sie über das Regler-Symbol die Zahlen aus, die Sie beobachten. |
| **KI-Datenverkehr** | Wo Anfragen bedient wurden, mit dem internen Anteil in der Mitte des Rings, die meistgenutzten Ziele, die Zahl der Anfragen, ihre Geschwindigkeit (P50, P95) und Fehler, für **Heute**, **24 Std.**, **7 T.** oder **30 T.** | **Datenverkehr ansehen** öffnet die Kosten- und Nutzungsseiten. |
| **Handlungsbedarf** | Aktuelle Probleme, die schwersten zuerst (siehe unten). | Beheben Sie sie, oder schalten Sie stumm, was Ihnen schon bekannt ist. |
| **Lokale KI-Kapazität** | Cluster in Ordnung, Maschinen online, GPUs und freie GPUs sowie CPU, GPU und VRAM jeder Maschine. **Einfärben nach**: **Zustand**, **GPU** oder **VRAM**. | Wählen Sie eine Maschine oder Gruppe für Details; **Maschinen** öffnet die Maschinen. |
| **Nutzung** | Anfragen pro Tag für die letzten **7 Tage** oder **30 Tage**, nach Ort der Bearbeitung. | Siehe [Nutzung und Kosten](usage.md). |
| **Integrationen** | **LLM-Provider** nach Standort und wie viele davon antworten, funktionsfähige **MCP-Server**, aktive **Skills**. | Jeder Eintrag öffnet seine Seite. |
| **Plattformzustand** | Der Gesamtzustand, die Dienste, von denen Fadenstack abhängt (Datenbank, Message Queue, Grafana), die Module, Gateway-Fehler in der letzten Stunde und belegte Datenbankverbindungen. | Eine ausgefallene Abhängigkeit erscheint auch unter **Handlungsbedarf**. |
| **Serverressourcen** | CPU, Arbeitsspeicher, Swap, Festplatte und GPU des Servers, auf dem Fadenstack läuft. | Wenig Speicherplatz erscheint unter **Handlungsbedarf**. |
| **Top-Container** | Die Container mit der höchsten CPU- und Speichernutzung. Anfangs ausgeblendet. | **Container** öffnet sie. |
| **Grafana** | Das Grafana-Übersichtsdashboard. Anfangs ausgeblendet. | |

??? info "Wenn eine Karte nicht lädt"
    Jede Karte lädt und aktualisiert sich für sich. Eine Karte, die nicht laden kann, zeigt „Dieses Widget
    konnte nicht geladen werden.“ mit **Erneut versuchen**, und die anderen arbeiten weiter.

### Wo Anfragen bedient wurden {#where-requests-were-served}

**KI-Datenverkehr**, **Nutzung**, **Integrationen** und **Intern bearbeitet** verwenden vier Klassen: **Eigene
Maschinen** (Ihre GPU-Maschinen und dieser Server), **Privates Netz**, **Extern** (ein Cloud-Provider) und
**Unbekannt** (wo es sich nicht feststellen lässt, etwa bei einem inzwischen entfernten Provider). *Intern* sind
die eigenen Maschinen plus das private Netz. Eine Anfrage, die abgelehnt wurde, bevor sie ein Modell erreichte,
wird gesondert gezählt („Vor der Weiterleitung abgelehnt“). Die Klassen sind dieselben wie auf der Seite
[Datenresidenz](providers.md#data-residency).

### Zustand der Maschinen {#machine-health}

Der Zustand einer Maschine ist das, was die Maschine meldet, nie ihre Auslastung: Eine GPU bei 100 % auf einer
Maschine, die in Ordnung ist, gilt als in Ordnung.

??? info "Mehr zur Karte „Lokale KI-Kapazität“"
    VRAM wird als belegt angezeigt: Ein laufendes Modell reserviert bewusst den größten Teil des Speichers seines
    Beschleunigers. Bei vielen Maschinen zeichnet die Karte eine kleine Zelle pro Maschine, dann eine
    Zusammenfassung pro Gruppe; wählen Sie eine Gruppe, um ihre Maschinen aufzulisten.

### Kennzahlen {#key-metrics}

| Kennzahl | Zeigt | Verglichen mit |
|---|---|---|
| **Anfragen heute** | Anfragen und Tokens | Gestern zur selben Uhrzeit |
| **Aktive Benutzer heute** | Benutzer heute und in diesem Monat | Gestern zur selben Uhrzeit |
| **Intern bearbeitet** | Den Anteil der Anfragen, die auf Ihren Maschinen oder in Ihrem privaten Netz bedient wurden | Gestern zur selben Uhrzeit |
| **Kosten diesen Monat** | Geschätzt aus den Modellpreisen; wie viele Anfragen an externe Modelle keinen Preis hatten | Vormonat bis zum selben Tag |
| **Handlungsbedarf** | Offene Probleme, kritisch und Warnung | |
| **Fehlerquote (1 Std.)** | Gateway-Fehler und die P95-Antwortzeit | Die Stunde davor |
| **GPUs in Benutzung** | GPUs mit mindestens 10 % Auslastung und Maschinen online | |

Die Farbe eines Trends sagt, ob die Änderung gut oder schlecht ist, nicht, ob der Wert gestiegen ist: Mehr Fehler
sind rot, ein größerer interner Anteil ist grün. Zeiten folgen der Zeitzone Ihres Browsers.

## Handlungsbedarf {#needs-attention}

![Die Karte „Handlungsbedarf“ mit einer fehlgeschlagenen Bereitstellung, wann sie gemeldet wurde, und der Glocke zum Stummschalten](../images/administer/needs-attention.png)

| Problem | Was zu tun ist |
|---|---|
| Eine Maschine **ist offline**, **meldet sich nicht mehr** oder **meldet** einen schlechten Zustand | Ihr Agent ist nicht verbunden oder sendet nicht. Prüfen Sie die Maschine (siehe [GPU-Maschinen](../operate/machines.md)). |
| **Maschinen warten auf Aufnahme** | Genehmigen oder lehnen Sie sie unter **KI-Infrastruktur → Maschinen** ab. |
| Die Temperatur einer GPU ist hoch | Prüfen Sie Kühlung und Luftstrom der Maschine. |
| Ein Cluster **ist beeinträchtigt** oder **fehlgeschlagen** | Öffnen Sie den Cluster, um den Grund zu sehen (siehe [Cluster](../operate/clusters.md)). |
| Eine Bereitstellung **ist fehlgeschlagen** oder **beeinträchtigt** | Öffnen Sie sie und lesen Sie ihre Meldung und ihr Log (siehe [Bereitstellungen](deployments.md#when-it-does-not-work)). |
| Ein Provider **antwortet nicht** | Anfragen gehen nicht an ihn, bis er wieder antwortet. Prüfen Sie den Provider (siehe [Externe Provider](providers.md)). |
| **Fehlgeschlagene Modell-Downloads** | Versuchen Sie es unter **Modell-Marktplatz → Auf diesem Server** erneut. |
| **Wartende Tool-Änderungen bei** einem Agenten | Prüfen Sie sie im Reiter **Apps** des Agenten (siehe [Agenten und Add-ins](agents.md#when-an-application-brings-new-tools)). |
| **Gateway-Serverfehler** | Viele Anfragen schlagen fehl. Lesen Sie die Logs (siehe [Fehler und Logs](../operate/failures.md)). |
| Eine Abhängigkeit **ist ausgefallen**, **Wenig Speicherplatz**, **Container starten neu** | Ein Problem mit dem Server selbst: siehe [Fehlerbehebung](../operate/troubleshooting.md). |
| **Fehlgeschlagene Dokumentaufnahme** | Versuchen Sie die Dokumente in der Wissensdatenbank erneut. |
| Ein **MCP-Server** ist fehlerhaft | Seine Tools stehen Chats nicht zur Verfügung. Öffnen Sie ihn und wählen Sie **Testen** (siehe [MCP-Server](mcp-servers.md)). |
| Ein HTTPS- oder Modell-Zertifikat läuft bald ab oder ist abgelaufen | Siehe [HTTPS und Vertrauen](../operate/https.md). |
| **Fadenstack *Version* ist erschienen**, ein **Sicherheitsupdate** oder ein **Kritisches Update** | Öffnen Sie **Updates**: Dort steht, was das Release behebt und welche Befehle Sie ausführen (siehe [Upgrade](../operate/upgrading.md#when-a-release-is-out)). Wer ein Release stummschaltet, schaltet das nächste nicht mit stumm. |
| Fadenstack *Version* **wird nicht mehr unterstützt**, **Bestandteile mit anderer Version**, **Maschinen mit älterem Agenten**, **Die Update-Prüfung funktioniert nicht** | Siehe [Upgrade](../operate/upgrading.md#how-you-hear-about-a-new-release). |
| **Modellverkehr zu** einem Cluster **ist nicht verschlüsselt** | Schalten Sie die Verschlüsselung auf der Seite des Clusters ein; seine Modelle laden neu (siehe [Cluster](../operate/clusters.md)). |

Um ein Problem beiseitezulegen, das Ihnen schon bekannt ist, wählen Sie **Stummschalten** (die Glocke daneben).
Es wird für alle Administratoren stummgeschaltet. **Wieder anzeigen** holt es zurück.

??? info "Mehr zum Stummschalten"
    Ein stummgeschaltetes Problem wandert unter **N stummgeschaltet** ans Ende der Liste und zählt in der
    Kopfzeile nicht mehr mit; die Liste zeigt, wer es wann stummgeschaltet hat. Eine Stummschaltung gilt,
    solange das Problem besteht: Sobald es behoben ist, entfällt sie, und dasselbe Problem wird bei seiner
    Rückkehr als neu angezeigt. Ein stummgeschaltetes Problem, das sich verschlimmert, etwa eine Warnung, die
    kritisch wird, wird wieder angezeigt.

## Anordnen {#arrange-it}

1. Wählen Sie **Anpassen**.
2. Ziehen Sie eine Karte an ihrem Griff, um sie zu verschieben.
3. Wählen Sie ihre Größe: **Klein**, **Mittel**, **Groß** oder **Volle Breite**.
4. Blenden Sie sie mit dem Auge aus oder ein. Die Reihen rücken um eine ausgeblendete Karte zusammen.
5. Wählen Sie **Fertig**.

**Layout zurücksetzen** stellt die Standardanordnung wieder her. Ihr Layout wird für Sie auf dem Server
gespeichert und folgt Ihnen so in einen anderen Browser oder auf ein anderes Gerät.

??? info "Mehr zum Layout"
    Wenn Sie es auf zwei Geräten gleichzeitig ändern, meldet das zweite Speichern „Ihr Layout wurde auf einem
    anderen Gerät geändert.“: **Meines behalten** überschreibt die andere Fassung, **OK** übernimmt sie. Auf
    einem Smartphone stapeln sich die Karten in einer Spalte.
