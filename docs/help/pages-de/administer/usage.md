# Nutzung und Limits {#usage-and-limits}

Fadenstack zählt jede Anfrage, die durch sein Gateway geht: wie viele Tokens sie verbraucht hat, welches Modell
geantwortet hat und was sie zu den von Ihnen festgelegten Preisen kosten würde. Diese Seite zeigt, was gezählt
wird, wo jede Person und die Administratoren es sehen und wie Sie begrenzen, was die ganze Instanz verbraucht.
Was die Zahlen für die Daten der Beschäftigten bedeuten und wer wessen Zahlen sehen darf, steht unter
[Nutzung und Datenschutz](usage-privacy.md).

## Wo Sie es sehen {#where-to-see-it}

| Wo | Wer | Was es zeigt |
|---|---|---|
| **Meine Nutzung**, im Menü hinter der Konto-Schaltfläche | Jede Person, für sich selbst | Die eigenen Tokens und geschätzten Kosten, die geltenden Limits und einen Download der eigenen Daten (siehe [Ihre eigene Nutzung](#your-own-usage)). |
| **Observability → Nutzung** | Administratoren, Prüfer und Nutzungsprüfer | Summen und Verlauf nach Team, Agent, Modell oder Provider, nur für Gruppen von mindestens fünf Personen (siehe [Die Seite „Nutzung“](#the-usage-page)). |
| [Dashboard](dashboard.md) | Administratoren | Den heutigen Tag auf einen Blick: **Anfragen heute**, **Aktive Benutzer heute**, **Intern bearbeitet**, **Kosten diesen Monat**, **Fehlerquote (1 Std.)**; **KI-Datenverkehr** (wo Anfragen bedient wurden) und **Nutzung** (Anfragen pro Tag, nach Ort der Bearbeitung). |
| **Observability → Budgets** | Administratoren, mit Fadenstack Enterprise | Was jedes Budget in diesem Zeitraum verbraucht hat, seine Benachrichtigungen und die Verrechnung je Team oder Kostenstelle (siehe [Budgets](budgets.md)). |
| **Observability → Kosten-Dashboard** | Administratoren | Ausgaben, Anfragen, Tokens, Geschwindigkeit und Fehler der ganzen Instanz sowie die Preise (siehe [Die Kosten- und Nutzungsseiten](#the-cost-and-usage-pages)). |
| **Observability → LLM-Aufrufe** | Administratoren | Jede Anfrage und jeder Aufruf: warum einer fehlschlug, was ein Agent für eine Frage tat, wohin Aufrufe gingen (siehe [LLM-Aufrufe](llm-calls.md)). |
| Der Reiter **Aktivität** eines Agenten | Administratoren; der Eigentümer eines persönlichen Agenten | Anfragen, Fehler, Tokens, Benutzer und Unterhaltungen dieses Agenten, nur als Zahlen, wenn mindestens fünf Personen ihn im Zeitraum genutzt haben; die Zahlen eines persönlichen Agenten sieht sein Eigentümer (siehe [Agenten und Add-ins](agents.md)). |
| Die Seite einer Bereitstellung | Administratoren | Anfragen, Geschwindigkeit und Fehler dieses Modells in den letzten Minuten (siehe [Bereitstellungen](deployments.md)). |
| **Observability → Datenresidenz** | Administratoren | Wie viele Daten an Ihre Maschinen, in Ihr Netz und nach außen gingen (siehe [Datenresidenz](providers.md#data-residency)). |

## Was gezählt wird {#what-is-counted}

Gezählt wird alles, was durch das Gateway geht: der Chat, die Agenten und Anwendungen, die API-Tokens nutzen.
Für jede Anfrage hält das Anfrageprotokoll fest:

- den Benutzer, seine **Nutzungsgruppe** (sein Team) und das Chat-Projekt oder den Agenten, von dem sie kam;
- das Modell und wo es lief: auf Ihren Maschinen, in Ihrem Netz oder bei einem externen Provider;
- die Tokens, die Zeit bis zum ersten Token und die Gesamtdauer;
- das Ergebnis: Erfolg, den Fehler oder das Limit, das sie abgelehnt hat;
- die geschätzten Kosten, wenn das Modell einen Preis hat.

Der Text der Anfrage und ihrer Antwort gehört nicht dazu. Alle paar Minuten fasst Fadenstack das
Anfrageprotokoll zu stündlichen und täglichen Zahlen zusammen; diese lesen die Nutzungsseiten. Was genau
gespeichert wird und wie lange, steht unter [Nutzung und Datenschutz](usage-privacy.md#the-data-fields).

Jede Zahl auf den Nutzungsseiten zählt:

| Zahl | Was sie zählt |
|---|---|
| **Anfragen** | Jede Anfrage, die das Gateway erreicht hat, fehlgeschlagene und abgelehnte eingeschlossen. Darunter, wie viele davon: |
| … fehlgeschlagen | Mit einem Fehler geendet (HTTP-Status 400 oder höher). Ablehnungen durch ein Limit zählen hier nicht. |
| … von einem Limit abgelehnt | Abgelehnt, bevor sie liefen (siehe [Limits für die ganze Instanz](#limits-for-the-whole-instance)). Sie haben keine Tokens verbraucht. |
| **Eingabe-Tokens** | Was an das Modell ging, einschließlich zwischengespeicherter Tokens. Darunter, wie viele *davon aus dem Cache*: der Teil, den das Modell oder sein Provider aus einem Cache genommen hat. |
| **Ausgabe-Tokens** | Was das Modell geschrieben hat, einschließlich seines Nachdenkens. Darunter, wie viele *davon für Reasoning*: der Teil, den ein Reasoning-Modell zum Nachdenken brauchte, bevor es antwortete. |
| **Geschätzte Kosten** | Die Tokens zum Preis des Modells (siehe [Woher die Zahlen kommen](#where-the-numbers-come-from)), je Währung. |
| **Personen** | Wie viele verschiedene Personen die Zahl umfasst. |

??? info "Tage, Monate und die Zeitzone"
    Tage und Monate beginnen um Mitternacht in der Zeitzone der Nutzungsmessung: **Einstellungen → Nutzung und
    Datenschutz**, **Zeitzone** (`usage.time_zone`). Wenn Sie sie nicht ändern, ist es die Zeitzone des
    Servers oder UTC, wenn diese nicht bekannt ist. Das Tageslimit beginnt um dieselbe Mitternacht neu.

## Ihre eigene Nutzung {#your-own-usage}

Jede Person, die sich anmeldet, sieht ihre eigene Nutzung unter **Meine Nutzung**, im Menü hinter ihrer
Konto-Schaltfläche:

- ihre Tokens und geschätzten Kosten nach Tag oder Monat, nach Modell, nach Agent und nach Chat-Projekt;
- die Limits, die für die Instanz gelten. Beim Tageslimit für Tokens zeigt sie auch, wie viel heute verbraucht
  ist, aber nur, wenn heute mindestens fünf andere Personen die Instanz genutzt haben (die
  [Mindestgruppengröße](#the-minimum-group-size)): Bei weniger zeigte die Zahl, was sie verbraucht haben. Die
  Limits pro Minute zeigen nur das Limit, nie, wie viel verbraucht ist;
- mit Fadenstack Enterprise die Budgets, die für sie gelten (siehe [Budgets](budgets.md#your-own-budgets));
- ob die personenbezogene Auswertung eingeschaltet ist, wie fein und wie lange ihre Zahlen aufbewahrt werden
  und wie oft ein Nutzungsprüfer sie angesehen hat, mit einem Link zu Ihren Datenschutzhinweisen, wenn Sie
  einen gesetzt haben (siehe [Nutzung und Datenschutz](usage-privacy.md#the-settings));
- unter **Ihre Daten** **Als JSON herunterladen** und **Als CSV herunterladen**.

Der Download enthält ihre täglichen Zahlen, ihre Einträge, die noch im Anfrageprotokoll stehen (Zeit, Modell,
Agent, Tokens, Kosten und Ergebnis; nie, was geschrieben wurde, nie das Gerät), sowie Datum und angegebenen
Grund jeder Einsicht in ihre Zahlen, ohne den Namen derjenigen, die hineingesehen haben. Er beantwortet, was
eine Person nach Art. 15 DSGVO über ihre Nutzung erfragen kann (siehe
[Nutzung und Datenschutz](usage-privacy.md#your-own-view-and-the-download)).

## Die Seite „Nutzung“ {#the-usage-page}

Die Seite **Nutzung** zeigt, wie viel die Instanz verbraucht und wo, ohne die Zahlen einzelner Personen zu
zeigen. Administratoren können sie öffnen, ebenso Personen mit der Rolle `auditor` oder `usage_auditor`, die
dafür keine Administratorrechte brauchen (siehe [Administratoren und Prüfer](users.md#administrators-and-auditors)).

1. Öffnen Sie **Observability → Nutzung**.
2. Wählen Sie den **Zeitraum** (**7 Tage** bis **12 Monate**) und **Zahlen pro** **Stunde**, **Tag** oder
   **Monat**.
3. Wählen Sie unter **Gruppieren nach** **Nutzungsgruppe** (das Team), **Agent**, **Modell** oder **Anbieter**.

Die Seite zeigt die **Summen**, **Tokens im Zeitverlauf** und eine Zeile je Team, Agent, Modell oder Provider, mit den Zahlen aus
[Was gezählt wird](#what-is-counted). Sie zeigt nie eine Person, einen Namen oder eine Rangfolge von Personen.
Wer die Zahlen einer einzelnen Person ansehen darf und wie, steht unter
[Nutzung und Datenschutz](usage-privacy.md#looking-at-one-persons-figures).

### Teams auf der Seite „Nutzung“ {#teams-on-the-usage-page}

Mit **Gruppieren nach** **Nutzungsgruppe** erscheinen die Teams als Baum, wie unter **Zugriffskontrolle →
Gruppen** festgelegt (siehe [Teams als Baum](users.md#teams-in-a-tree)). Öffnen Sie ein Team, um seine Unterteams
zu sehen.

- **Die Zahlen eines Teams enthalten die seiner Unterteams.** Die Nutzung einer Person zählt für ihre
  Nutzungsgruppe und für jedes Team darüber.
- **Ein Team erscheint, wenn in ihm und seinen Unterteams zusammen mindestens fünf Personen aktiv waren.**
  Kleinere Unterteams werden in einer Zeile „kleinere Teams“ unter ihrem übergeordneten Team zusammengefasst,
  geschützt wie die Zeile **Weniger als 5 Personen** (siehe [Die Mindestgruppengröße](#the-minimum-group-size)).
- **Personen ohne Nutzungsgruppe** werden unter **Nicht zugeordnet** gezählt.
- **Wie erfasst** oder **Heutige Struktur** bestimmt, welchem Baum die Zahlen folgen. **Wie erfasst**, die
  Voreinstellung, zählt die Nutzung für die Teams, wie sie zum Zeitpunkt der Nutzung bestanden; eine
  Umorganisation schreibt die Vergangenheit also nicht um. **Heutige Struktur** zählt alles für den heutigen
  Baum, etwa um das Vorjahr mit den neuen Abteilungen zu vergleichen.

### Die Mindestgruppengröße {#the-minimum-group-size}

Jede Zahl auf der Seite umfasst mindestens fünf verschiedene Personen: die **Mindestgruppengröße**. Sie können
sie unter **Einstellungen → Nutzung und Datenschutz** erhöhen, aber nicht unter fünf setzen.

- Ein Team, ein Agent, ein Modell oder ein Provider, den im Zeitraum weniger Personen genutzt haben, bekommt keine
  eigene Zeile. Er wird in der Zeile **Weniger als 5 Personen** gezählt, die alle diese Gruppen enthält.
- Auch diese Zeile umfasst mindestens fünf Personen. Sind die kleinen Gruppen zusammen weniger, kommen die
  kleinsten angezeigten Gruppen hinzu, bis es reicht. So lässt sich keine Gruppe aus der Summe minus den übrigen
  Zeilen errechnen.
- Eine Stunde, ein Tag oder ein Monat, in dem weniger Personen die Instanz genutzt haben, zeigt keine Zahlen.
  Stunden erscheinen nur, wenn mindestens fünf Personen die Instanz in dieser Stunde genutzt haben.
- Dasselbe gilt über die Zeit: Ließe sich ein ausgeblendeter Zeitraum aus der Summe minus den angezeigten
  errechnen, wird ein weiterer mit ihm ausgeblendet.
- **Summen** und **Tokens im Zeitverlauf** zeigen nur Zahlen, wenn mindestens fünf Personen die Instanz im
  Zeitraum genutzt haben. Auf einer so kleinen Instanz darf ein Nutzungsprüfer sie trotzdem zeigen, solange die
  personenbezogene Auswertung eingeschaltet ist: mit **Trotzdem zeigen** und einer Begründung, die festgehalten
  wird, pro Tag oder Monat, nie pro Stunde. Die Begründung gilt für diese eine Ansicht: Wer **Zeitraum**,
  **Zahlen pro** oder **Gruppieren nach** ändert, blendet die Zahlen wieder aus.

Ein Team aus drei Personen oder ein Agent, den eine Person nutzt, erscheint daher nie allein: Seine Zahlen wären
die einer Person unter anderem Namen.

!!! note "Kleine Instanzen"
    Haben im Zeitraum weniger als fünf Personen die Instanz genutzt, sagt die Seite das (**Eine kleine
    Instanz**), und die Summen zeigen keine Zahlen: Sie lägen nahe an den Zahlen einer einzelnen Person. Ein
    Nutzungsprüfer, der sie bei eingeschalteter personenbezogener Auswertung trotzdem zeigt, behandelt sie wie
    personenbezogene Daten (siehe [Nutzung und Datenschutz](usage-privacy.md)).

## Die Kosten- und Nutzungsseiten {#the-cost-and-usage-pages}

![Kosten-Observability auf dem Reiter „Übersicht“: Geschätzte Ausgaben, Anfragen gesamt, Tokens gesamt, Durchschn. Latenz und Fehlerrate, darunter Kosten im Zeitverlauf, Anfrage-Durchsatz, Latenz-Perzentile und Ausgaben nach Modell](../images/administer/observability.png)

Öffnen Sie **Observability → Kosten-Dashboard** (die Seite heißt **Kosten-Observability**) und wählen Sie einen
Zeitraum.

![Kosten-Observability, geöffnet über Observability → Kosten-Dashboard: die Reiter Übersicht, Anfragen und Preise sowie die Zeiträume 7d bis 90d mit Aktualisieren und Herunterladen oben rechts](../images/administer/cost-dashboard-top.png)

| Reiter | Was er zeigt |
|---|---|
| **Übersicht** | **Geschätzte Ausgaben**, **Anfragen gesamt**, **Tokens gesamt**, **Durchschn. Latenz** und **Fehlerrate**, dazu **Kosten im Zeitverlauf**, **Anfrage-Durchsatz**, **Latenz-Perzentile** und **Ausgaben nach Modell**. |
| **Anfragen** | Jede Anfrage, die neueste zuerst. Filtern Sie nach **Modell** und öffnen Sie eine Anfrage, um ihre Details zu sehen: Tokens, Kosten, Zeiten, die Tools und Skills, die sie genutzt hat, und die Schritte, die sie durchlaufen hat. |
| **Preise** | Die Preise, mit denen die Schätzungen rechnen (siehe [Preise festlegen](#set-prices)). |

**CSV exportieren** lädt die Anfragen des Zeitraums (bis zu 5.000 Zeilen) für eine Tabellenkalkulation herunter.

Die Anfragen zeigen nie, wer sie gesendet hat: weder in der Liste noch in den Details oder im Export, und auch
nicht unter **LLM-Aufrufe**. Die Nutzung einer einzelnen Person zeigt nur die personenbezogene Auswertung unter
**Nutzung** (siehe [Nutzung und Datenschutz](usage-privacy.md#looking-at-one-persons-figures)).

## Limits für die ganze Instanz {#limits-for-the-whole-instance}

Drei Limits verhindern, dass die Instanz aufgebraucht wird, etwa durch ein außer Kontrolle geratenes Skript oder
einen Agenten, der sich im Kreis dreht. Jedes zählt die Anfragen aller zusammen, über den Chat, die Agenten und
die API, und hält nichts über einzelne Personen fest.

| Limit | Was es begrenzt | Beginnt neu |
|---|---|---|
| **Anfragen pro Minute** | Die Anfragen aller zusammen | Jede Minute |
| **Tokens pro Minute** | Eingabe- und Ausgabe-Tokens aller zusammen | Jede Minute |
| **Tokens pro Tag** | Eingabe- und Ausgabe-Tokens aller zusammen | Um Mitternacht in der Zeitzone der Nutzungsmessung |

1. Öffnen Sie **Einstellungen** und wählen Sie den Reiter **Nutzung und Datenschutz**.
2. Tragen Sie unter **Limits der Instanz** für jedes Limit, das Sie wollen, eine Zahl ein. Lassen Sie ein Feld leer, wenn es
   kein Limit geben soll.
3. Wählen Sie **Speichern**.

Die Änderung wird mit Ihrem Namen im Audit-Protokoll festgehalten (`usage.limits.update`). **Meine Nutzung**
zeigt allen die Limits und, wo das auf niemanden hinweist, wie viel vom Tageslimit für Tokens heute verbraucht
ist (siehe [Ihre eigene Nutzung](#your-own-usage)).

??? info "Wie ein Limit eine Anfrage zählt"
    - Eine Anfrage zählt, sobald sie beginnt: mit ihrem Prompt und dem Höchsten, was sie schreiben darf (ihrem
      `max_tokens` oder einem Standardwert, wenn sie keinen setzt). Ist die Antwort fertig, treten ihre
      tatsächlichen Tokens an diese Stelle.
    - Eine Anfrage, die ein Limit ablehnt, zählt nicht dafür.
    - Eine Antwort, die bereits geschrieben wird, wird nie abgebrochen: Ihre Tokens zählen, sobald sie fertig
      ist. Die Instanz kann ein Limit daher leicht überschreiten.

Budgets pro Team, pro Agent, pro Person oder für die ganze Organisation, pro Tag, Woche oder Monat, gibt es mit
Fadenstack Enterprise: siehe [Budgets](budgets.md).

### Was Benutzer sehen, wenn ein Limit ablehnt {#what-people-see-when-a-limit-refuses}

Im Chat und in den Agenten erhalten Benutzer eine Meldung, die das Limit nennt, etwa „this instance's daily token
limit“, und sie bittet, sich an einen Administrator zu wenden. Sie sagt nichts über die Nutzung anderer, und
niemand sonst wird benachrichtigt.

Anwendungen erhalten die Antwort, die die API von OpenAI gibt, und ihre Client-Bibliotheken behandeln sie wie
dort: HTTP-Status `429` mit einer Fehlermeldung im Format von OpenAI (`error.message`, `error.type`,
`error.code`).

| Limit | `error.type` | `error.code` | `Retry-After` |
|---|---|---|---|
| Anfragen pro Minute | `rate_limit_error` | `rate_limit_rpm` | Sekunden bis zum Ende der Minute |
| Tokens pro Minute | `rate_limit_error` | `rate_limit_tpm` | Sekunden bis zum Ende der Minute |
| Tokens pro Tag | `insufficient_quota` | `quota_tokens_per_day` | Sekunden bis zum nächsten Tag, mit `x-should-retry: false` |

Die Client-Bibliotheken von OpenAI warten nach einer Ablehnung pro Minute und versuchen es von selbst erneut;
nach einer Ablehnung pro Tag (`x-should-retry: false`) nicht. Solange ein Limit gesetzt ist, trägt jede Antwort,
wie viel übrig ist: `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests` und
`x-ratelimit-reset-requests` sowie dieselben drei für `-tokens` (die Zeit bis zum Neubeginn so, wie OpenAI sie
schreibt, z. B. `12s`).

## Woher die Zahlen kommen {#where-the-numbers-come-from}

- **Tokens** sind, was der Provider des Modells für die Anfrage gemeldet hat. Meldet er keine, zählt Fadenstack
  sie selbst oder schätzt sie; das Anfrageprotokoll hält fest, welches davon (`provider`, `counted` oder
  `estimated`).
- **Kosten** sind Schätzungen: die Tokens zu dem Preis, der für das Modell galt, als die Anfrage lief. Sie
  ändern nichts an dem, was ein Provider Ihnen in Rechnung stellt. Ein Modell ohne Preis verursacht keine
  Kosten; Zahlen in mehreren Währungen erscheinen je Währung, nie umgerechnet.
- **Die Nutzungsseiten** fassen das Anfrageprotokoll alle paar Minuten zusammen; eine Anfrage erscheint dort
  daher einige Minuten, nachdem sie geendet hat.

### Preise festlegen {#set-prices}

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

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| Die Zahlen sind leer | Im Zeitraum gibt es keine Anfragen, oder das Gateway kann sein Protokoll nicht schreiben. Senden Sie eine Nachricht im Chat, warten Sie ein paar Minuten und aktualisieren Sie dann die Seite. |
| Eine Anfrage von eben fehlt unter **Nutzung** oder **Meine Nutzung** | Die Nutzungsseiten fassen das Anfrageprotokoll alle paar Minuten zusammen. Aktualisieren Sie etwas später. |
| Eine Zeile lautet **Weniger als 5 Personen**, oder ein Tag zeigt keine Zahlen | Weniger als fünf Personen haben dieses Team, diesen Agenten, dieses Modell oder diesen Tag genutzt: Ihre Zahlen werden absichtlich zusammengefasst (siehe [Die Mindestgruppengröße](#the-minimum-group-size)). |
| Die Kosten zeigen „Keine“ oder wirken zu niedrig | Die verwendeten Modelle haben keinen Preis. Legen Sie ihn wie oben beschrieben fest. |
| Benutzer erhalten eine Meldung über das Limit der Instanz | Ein Limit für die ganze Instanz ist erreicht. Erhöhen Sie es unter **Einstellungen → Nutzung und Datenschutz** oder warten Sie, bis es neu beginnt: in der nächsten Minute oder, beim Tageslimit, um Mitternacht. |
| Eine Anwendung bricht mit `429` und `quota_tokens_per_day` ab | Das Tageslimit für Tokens ist erreicht; der Client versucht es nicht von selbst erneut. Es geht nach Mitternacht in der Zeitzone der Nutzungsmessung wieder, oder sobald Sie das Limit erhöhen. |
| Eine Anfrage fehlt auf den Kostenseiten | Gezählt werden nur Anfragen, die das Gateway erreichen: Ein Modell, das jemand direkt an seinem Endpunkt aufruft, an Fadenstack vorbei, wird nicht gezählt. |
