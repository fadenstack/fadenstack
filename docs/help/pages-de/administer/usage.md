# Nutzung und Kosten {#usage-and-costs}

Fadenstack zählt jede Anfrage, die durch sein Gateway geht: wer sie gesendet hat, welches Modell geantwortet hat
und wo es lief, wie viele Tokens sie verbraucht hat, wie lange sie gedauert hat und was sie zu den von Ihnen
festgelegten Preisen kosten würde. Diese Seite zeigt, wo Sie diese Zahlen finden und wie die Kostenschätzungen
aussagekräftig werden.

![Kosten-Observability auf dem Reiter „Übersicht“: Geschätzte Ausgaben, Anfragen gesamt, Tokens gesamt, Durchschn. Latenz und Fehlerrate, darunter Kosten im Zeitverlauf, Anfrage-Durchsatz, Latenz-Perzentile und Ausgaben nach Modell](../images/administer/observability.png)

## Was gezählt wird {#what-is-counted}

Für jede Anfrage hält das Anfrageprotokoll fest:

- den Benutzer, seine **Nutzungsgruppe** und das Chat-Projekt oder den Agenten, von dem sie kam;
- das Modell und wo es lief: auf Ihren Maschinen, in Ihrem Netz oder bei einem externen Provider;
- die Tokens: Eingabe, Ausgabe und gesamt;
- die Zeit bis zum ersten Token und die Gesamtdauer;
- das Ergebnis: Erfolg oder den Fehler;
- die geschätzten Kosten, wenn das Modell einen Preis hat.

Der Text der Anfrage und ihrer Antwort gehört nicht dazu. Alles, was durch das Gateway geht, wird gezählt: der
Chat, die Agenten und Anwendungen, die API-Tokens nutzen.

## Wo Sie es sehen {#where-to-see-it}

| Wo | Was es zeigt |
|---|---|
| [Dashboard](dashboard.md) | Den heutigen Tag auf einen Blick: **Anfragen heute**, **Aktive Benutzer heute**, **Intern bearbeitet**, **Kosten diesen Monat**, **Fehlerquote (1 Std.)**; **KI-Datenverkehr** (wo Anfragen bedient wurden) und **Nutzung** (Anfragen pro Tag, nach Ort der Bearbeitung). |
| **Observability → Kosten-Dashboard** | Die Kosten- und Nutzungsseiten (siehe unten). |
| **Observability → LLM-Aufrufe** | Jede Anfrage und jeder Aufruf: warum einer fehlschlug, was ein Agent für eine Frage tat, wohin Aufrufe gingen (siehe [LLM-Aufrufe](llm-calls.md)). |
| Der Reiter **Aktivität** eines Agenten | Anfragen, Fehler, Tokens, Benutzer und Unterhaltungen dieses Agenten, nur als Zahlen (siehe [Agenten und Add-ins](agents.md)). |
| Die Seite einer Bereitstellung | Anfragen, Geschwindigkeit und Fehler dieses Modells in den letzten Minuten (siehe [Bereitstellungen](deployments.md)). |
| **Observability → Datenresidenz** | Wie viele Daten an Ihre Maschinen, in Ihr Netz und nach außen gingen (siehe [Datenresidenz](providers.md#data-residency)). |

## Die Kosten- und Nutzungsseiten {#the-cost-and-usage-pages}

![Kosten-Observability, geöffnet über Observability → Kosten-Dashboard: die Reiter Übersicht, Anfragen und Preise sowie die Zeiträume 7d bis 90d mit Aktualisieren und Herunterladen oben rechts](../images/administer/cost-dashboard-top.png)

Öffnen Sie **Observability → Kosten-Dashboard** (die Seite heißt **Kosten-Observability**) und wählen Sie einen
Zeitraum.

| Reiter | Was er zeigt |
|---|---|
| **Übersicht** | **Geschätzte Ausgaben**, **Anfragen gesamt**, **Tokens gesamt**, **Durchschn. Latenz** und **Fehlerrate**, dazu **Kosten im Zeitverlauf**, **Anfrage-Durchsatz**, **Latenz-Perzentile**, **Ausgaben nach Modell** und **Top-Benutzer**. |
| **Anfragen** | Jede Anfrage, die neueste zuerst. Filtern Sie nach **Modell** oder **Benutzer-ID** und öffnen Sie eine Anfrage, um ihre Details zu sehen: Tokens, Kosten, Zeiten, die Tools und Skills, die sie genutzt hat, und die Schritte, die sie durchlaufen hat. |
| **Preise** | Die Preise, mit denen die Schätzungen rechnen (siehe unten). |

**CSV exportieren** lädt die Anfragen des Zeitraums (bis zu 5.000 Zeilen) für eine Tabellenkalkulation herunter.

## Preise festlegen {#set-prices}

Kosten sind Schätzungen auf Grundlage der Preise, die Sie pro Modell festlegen. Sie ändern nichts an dem, was ein
Provider Ihnen in Rechnung stellt.

![LLM-Provider: die Zeile eines Providers mit seinem Modell, „Preis festlegen“ in der Spalte „Preis $/1 Mio.“, seinem Status und Endpunkt](../images/administer/providers-list-top.png)

1. Öffnen Sie **LLM → Provider** und wählen Sie in der Zeile des Modells **Preis festlegen** (in der Spalte
   **Preis $/1 Mio.**).

    ![Der Preisdialog für ein Modell: Eingabepreis und Ausgabepreis in USD pro Million Tokens, und Speichern](../images/administer/provider-set-price.png)

2. Geben Sie ein, was eine Million Eingabe-Tokens und eine Million Ausgabe-Tokens kosten.
3. Speichern Sie. Anfragen werden innerhalb einer Minute mit diesem Preis berechnet.

![Der Reiter „Preise“ der Kostenseiten: Eingabe- und Ausgabepreis jedes Modells pro Million Tokens, ob es ein Standardwert ist oder von einem Administrator festgelegt wurde, ab wann er gilt, und „Preis hinzufügen“](../images/administer/cost-pricing-list.png)

Sie können Preise auch festlegen, wenn Sie einen Provider anbinden, oder unter **Preise** auf den Kostenseiten,
wo der Preisverlauf jedes Modells aufbewahrt wird.

??? info "Welchen Preis Sie festlegen"
    Verwenden Sie für ein Cloud-Modell die Listenpreise des Providers. Für Ihre eigenen Modelle können Sie mit
    einem eigenen Preis (z. B. dem, was Sie die Hardware pro Million Tokens kostet) die interne Nutzung
    vergleichen und intern verrechnen; lassen Sie das Feld leer, wenn Sie das nicht brauchen.

Anfragen an ein externes Modell ohne Preis werden trotzdem gezählt. **Kosten diesen Monat** auf dem Dashboard
zeigt, wie viele es waren („Anfragen ohne Preis“), damit Sie wissen, dass die Schätzung zu niedrig ist.

## Nutzung pro Person und pro Gruppe {#usage-per-person-and-per-group}

![„Rollen zuweisen“ für eine Person: die Rollen zum Ankreuzen und die Nutzungsgruppe, der ihre Nutzung zugeordnet wird](../images/administer/users-edit-roles.png)

- **Pro Person**: **Top-Benutzer** auf den Kostenseiten und der Filter **Benutzer-ID** unter **Anfragen**.
- **Pro Gruppe**: Jede Anfrage wird mit der **Nutzungsgruppe** des Benutzers erfasst, also der Gruppe, die in
  seiner Zeile unter **Zugriffskontrolle → Benutzer** eingestellt ist (siehe
  [Benutzer, Gruppen und Rollen](users.md#groups)). Ein Benutzer ohne Nutzungsgruppe zählt zu keiner. Berichte
  über die Nutzung pro Gruppe in der Konsole sind <span class="fs-planned">Geplant</span>.

## Limits und Budgets {#limits-and-budgets}

Budgets und Limits pro Person, Gruppe oder Agent sind <span class="fs-planned">Geplant</span>: Sie werden
begrenzen, wie viel eine Person, ein Team oder ein Agent nutzen darf.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| Die Zahlen sind leer | Im Zeitraum gibt es keine Anfragen, oder das Gateway kann sein Protokoll nicht schreiben. Senden Sie eine Nachricht im Chat und aktualisieren Sie dann die Seite. |
| Die Kosten zeigen „Keine“ oder wirken zu niedrig | Die verwendeten Modelle haben keinen Preis. Legen Sie ihn wie oben beschrieben fest. |
| Eine Anfrage fehlt auf den Kostenseiten | Gezählt werden nur Anfragen, die das Gateway erreichen: Ein Modell, das jemand direkt an seinem Endpunkt aufruft, an Fadenstack vorbei, wird nicht gezählt. |
