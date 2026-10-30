# LLM-Aufrufe {#llm-calls}

Jeder Aufruf, der über das Gateway läuft – aus dem Chat, von einem Agenten oder von einer Anwendung über die
API –, mit seiner Dauer, seinen Tokens und seinem Ergebnis. Auf dieser Seite finden Sie den Aufruf, der
schiefging, und sehen, warum. Und Sie verfolgen, was ein Agent für eine Frage getan hat: jede Runde mit dem
Modell und die Tools, die seine App dazwischen ausführte.

![LLM-Aufrufe: oben die Zahlen der letzten 24 Stunden, darunter die Anfragen, die neueste zuerst, mit Runden, Dauer, Tokens und Tools](../images/administer/llm-calls.png)

## Bevor Sie beginnen {#before-you-start}

Öffnen Sie **Observability → LLM-Aufrufe**. Personen mit der Rolle `admin`, `operator` oder `viewer` sehen die
Seite (siehe [Benutzer, Gruppen und Rollen](users.md)).

## Was die Seite zeigt {#what-the-page-shows}

Oben stehen die Zahlen zu allem, was die Filter auswählen: **Aufrufe**, **Fehler** (wählen Sie es, um nur die
Fehlschläge zu sehen), **Latenz p50** und **Latenz p95**, **Erstes Token p95**, **Tokens ein → aus** und
**Gesch. Kosten**.

Darunter steht die Liste. **Anzeigen** wechselt zwischen zwei Ansichten derselben Aufrufe:

| Ansicht | Eine Zeile je |
|---|---|
| **Anfragen** | Frage, die jemand gestellt hat. Die App eines Agenten braucht für eine Frage oft mehrere **Runden** mit dem Modell: Das Modell bittet um Tools, die App führt sie aus, das Modell antwortet. Eine Anfrage fasst diese Runden zusammen. Ein Aufruf, der zu keiner Anfrage eines Agenten gehört, ist eine eigene Anfrage. |
| **Aufrufe** | Aufruf eines Modells, jede Runde in einer eigenen Zeile. |

| Spalte | Was sie zeigt |
|---|---|
| **Status** | Das Ergebnis: grün bei Erfolg, sonst der Code des Fehlers. |
| **Anfrage** / **Aufruf** | Wie viele Runden die Anfrage brauchte, oder welche Art von Aufruf es war. Der Roboter kennzeichnet den Aufruf eines Agenten. |
| **Dauer** | Vom Beginn der ersten Runde bis zum Ende der letzten, darunter der Anteil des Modells. |
| **Tokens** | Die Tokens, die an das Modell gingen, und die, die es schrieb. |
| **Tools** | Die Tools, die für die Anfrage liefen, und wie viele fehlschlugen; daneben die Freigaben, die eine Person erteilte oder ablehnte. |

Wählen Sie den Pfeil vor einer Anfrage, um ihre Runden zu sehen.

## Einen Aufruf finden {#find-a-call}

1. Wählen Sie oben einen Zeitraum: **15 Min** bis **30 Tage**.
2. Grenzen Sie die Liste ein: **Alle Modelle** für ein Modell, **Fehler** oder **Erfolgreich** für das
   Ergebnis, oder tippen Sie ins Suchfeld. Die Suche findet einen Aufruf an seiner Anfrage- oder Trace-ID und an
   einem Teil des Modellnamens, Endpunkts oder Fehlercodes. Nach Person, Sitzung, Unterhaltung, Gerät oder Agent
   findet sie keinen Aufruf.
3. Sortieren Sie mit den Spaltenköpfen nach **Zeit**, **Dauer** oder **Tokens**.

**Live** holt alle paar Sekunden neue Aufrufe, solange Sie die erste Seite ansehen; wählen Sie es erneut, um
anzuhalten.

Jeder Filter steht in der Adresse der Seite. Kopieren Sie die Adresse, oder wählen Sie in einem geöffneten
Aufruf das Link-Symbol, um einer Kollegin oder einem Kollegen genau das zu zeigen, was Sie sehen.

## Eine Anfrage oder einen Aufruf öffnen {#open-a-request-or-a-call}

Wählen Sie eine Zeile. Der Aufruf öffnet sich neben der Liste, auf einem kleinen Bildschirm darüber. Mit den
Pfeilen oben oder den Tasten `j` und `k` gehen Sie zum nächsten oder vorigen, mit `Esc` schließen Sie ihn.

![Eine Anfrage, neben der Liste geöffnet: Modell und Provider, Latenz, erstes Token, Tokens und Tempo, oben ein Befund und darunter die Runden der Anfrage](../images/administer/llm-calls-request.png)

**Überblick** beginnt mit dem, was der Datensatz des Aufrufs über einen Fehler sagt, falls es einen gibt (siehe
unten). Dann:

- **Anfrage**: bei einer Anfrage mit mehreren Runden ein Zeitverlauf der ganzen Anfrage. Jede Runde mit dem
  Modell, und dazwischen die Tools, die die App ausführte, und die Freigaben, auf die sie wartete. Wählen Sie
  eine Runde, um sie zu öffnen.
- **Zeitverlauf** (**Diese Runde** in einer Anfrage): wohin die Zeit dieses Aufrufs ging, das Warten auf das
  erste Token und das Schreiben, und die Tools, die das Gateway dafür ausführte.
- **Governance**: was eine Richtlinie über den Aufruf entschied, falls eine es tat.
- **Unterhaltung** oder **Trace**: die anderen Aufrufe, die zu diesem gehören, der Reihe nach, wenn es welche gibt.

![Der Zeitverlauf einer Anfrage eines Agenten: neun Runden, nach jeder die Freigabe und die Tool-Ausführung, um die das Modell bat](../images/administer/llm-calls-request-timeline.png)

**Details** listet die erfassten Felder des Aufrufs, ohne die Person und ohne die Sitzung, die Unterhaltung, das
Gerät oder das Projekt, zu denen er gehört, mit einer Schaltfläche, um die Liste nach dem Modell des Aufrufs zu
filtern. **JSON** zeigt den Datensatz, wie die Seite ihn hat, zum Kopieren in eine Fehlermeldung.

??? info "Woher die Tools der App kommen"
    Die Tools eines Agenten laufen in seiner App, auf dem Computer der Person, nicht auf dem Server. Die App
    meldet, welche Tools sie ausführte, wie lange sie brauchten und wie sie endeten, und um welche Freigaben sie
    bat. Was die Tools lasen oder schrieben, meldet sie nie.

    Manche Apps schicken diese Meldungen gesammelt am Ende einer Anfrage, ohne eigene Zeiten. Der Zeitverlauf
    setzt sie dann in der gemeldeten Reihenfolge in die Lücken zwischen den Runden und sagt das darunter.

## Warum ein Aufruf fehlschlug {#why-a-call-failed}

![Ein fehlgeschlagener Aufruf neben der Liste der Fehler: der Befund „Nach 15m 01s Wartezeit fehlgeschlagen" und was zu prüfen ist](../images/administer/llm-calls-error.png)

Jeder Befund nennt, was geschah, und was als Nächstes zu prüfen ist. Die häufigsten:

| Sie sehen | Was zu tun ist |
|---|---|
| **Nach … Wartezeit fehlgeschlagen** | Der Modellserver oder etwas zwischen ihm und dem Gateway hat aufgegeben. Prüfen Sie die Bereitstellung des Modells und ihre Protokolle auf Überlast oder eine hängende Kopie. |
| **Der Modellserver schickte einen Fehler in seiner Antwort** | Der Client erhielt eine Antwort, die mit einem Fehler abbrach. Meist ist der Prompt länger als der Kontext des Modells, oder ein Parameter wird nicht unterstützt. Die Protokolle der Bereitstellung nennen den Grund des Modellservers. |
| **Der Provider war nicht erreichbar** | Prüfen Sie die Adresse des Providers, ob er läuft, und die HTTPS-Einstellungen (siehe [Externe Provider](providers.md)). |
| **Kein Provider hatte freie Kapazität** | Jede Kopie des Modells war ausgelastet. Erhöhen Sie ihr Limit oder fügen Sie Kopien hinzu (siehe [Bereitstellungen](deployments.md)). |
| **Der Aufrufer war nicht berechtigt** | Die Anmeldung oder das API-Token fehlt, ist abgelaufen oder widerrufen, oder der Person fehlt die Berechtigung. |
| **Von einer Governance-Richtlinie blockiert** | Eine Richtlinie hat den Aufruf abgelehnt (siehe [Richtlinien und personenbezogene Daten](policies.md)). |
| **Die Antwort wurde bei … Tokens abgeschnitten** | Das Token-Limit des Aufrufs oder der Kontext des Modells war erschöpft. Erhöhen Sie das Limit oder kürzen Sie den Prompt. |
| **… Tool-Ausführung(en) in der App fehlgeschlagen** | Die App führte ein Tool aus, um das das Modell bat, und es schlug fehl. Das Protokoll der App nennt den Grund. |
| **Vom Benutzer abgelehnt** | Eine Person lehnte ein Tool ab, das das Modell ausführen wollte. Kein Fehler: Das Modell machte ohne es weiter. |
| **… auf Freigaben gewartet** | Ein Großteil der Zeit der Anfrage war eine Person am Entscheiden. |
| **Das Modell brauchte … Runden** | Viele Runden für eine Frage: Das Modell dreht sich vielleicht im Kreis, oder ein Tool schlägt immer wieder fehl. Vergleichen Sie die Runden. |

## Wohin Aufrufe gingen {#where-calls-went}

![Routing-Karte: das Gateway, die angefragten Modelle und die Provider, die antworteten, mit Aufrufen und Fehlerquoten an jeder Linie und einer Tabelle darunter](../images/administer/llm-calls-routing.png)

**Routing-Karte** zeichnet die Aufrufe des Zeitraums vom Gateway zu jedem angefragten Modell und weiter zum
Provider, der antwortete, mit der Zahl der Aufrufe und dem Anteil der Fehler an jeder Linie. Rote und orange
Linien zeigen, wo sich Fehler häufen. Die Tabelle darunter hat dieselben Zahlen, mit den langsamsten Aufrufen
(**Latenz p95**) und dem letzten Aufruf jeder Verbindung. Wählen Sie ein Modell, um seine Aufrufe zu sehen. Ist
in der Liste ein Aufruf geöffnet, hebt die Karte seinen Weg hervor.

## Was erfasst wird {#what-is-recorded}

Zu jedem Aufruf hält das Gateway fest, wer ihn schickte, welches Modell wo antwortete, die Zeiten, die Tokens,
das Ergebnis und die geschätzten Kosten; bei einem Agenten auch seine Unterhaltung und Anfrage. Der Text der
Anfrage und der Antwort wird nicht gespeichert, ebenso wenig, was ein Tool las oder schrieb. Die Zahlen hier
sind dieselben wie unter [Nutzung und Limits](usage.md).

Diese Seite zeigt nie, wer einen Aufruf geschickt hat, und findet Aufrufe nicht nach Person. Sie zeigt auch keine
Sitzungs-, Unterhaltungs-, Geräte- oder Projekt-ID und findet keinen Aufruf danach oder nach Agent: Jedes davon
führte zu einer Person. Die Nutzung einer einzelnen Person zeigt nur die personenbezogene Auswertung unter
**Nutzung**, einem Nutzungsprüfer, mit Begründung und aufgezeichnet (siehe
[Nutzung und Datenschutz](usage-privacy.md#looking-at-one-persons-figures)).

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was zu tun ist |
|---|---|
| **Keine passenden Aufrufe** | Im Zeitraum passt nichts zu den Filtern. Wählen Sie einen längeren Zeitraum, **Alle Modelle** und **Alle**, oder leeren Sie das Suchfeld. |
| Die Anfrage eines Agenten zeigt Runden, aber keine Tools | Das Modell bat um Tools, aber die App meldete keine Ausführung: eine App ohne Tool-Meldungen, oder die Meldungen sind noch unterwegs. Die Runden stehen trotzdem da. |
| Der Provider steht als **Instanz …** da | Der Provider, der den Aufruf bediente, wurde inzwischen entfernt. |
| **Gesch. Kosten** zeigt „—" | Die genutzten Modelle haben keinen Preis. Legen Sie einen fest (siehe [Nutzung und Limits](usage.md#set-prices)). |
