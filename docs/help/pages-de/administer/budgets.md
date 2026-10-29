# Budgets {#budgets}

Ein Budget begrenzt, wie viele Tokens oder wie viel Geld die ganze Organisation, ein Team, ein Agent oder eine
einzelne Person pro Tag, Woche oder Monat nutzen darf. Jede Anfrage muss in jedes Budget passen, das für sie
gilt; ein Budget kann ablehnen, was nicht passt, oder nur benachrichtigen. Dieselbe Seite liefert die monatliche
Verrechnung je Team oder Kostenstelle. Budgets erfordern Fadenstack Enterprise.

## Bevor Sie beginnen {#before-you-start}

- Sie sind in der Konsole als **Superuser** angemeldet.
- Ihre Teams sind als Baum eingerichtet, und jede Person hat eine **Nutzungsgruppe**: ihr Stammteam, in dem ihre
  Nutzung zählt (siehe [Teams als Baum](users.md#teams-in-a-tree)).
- Für Budgets in Geld haben die Modelle Preise (siehe [Preise festlegen](usage.md#set-prices)). Ein Modell ohne
  Preis trägt zu einem Budget in Geld nichts bei.
- Budgets für einzelne Personen sind ausgeschaltet, bis Sie sie einschalten; dafür brauchen Sie den
  **Root-Modus** (siehe [Die Einstellungen](#the-settings)).

## Ein Budget einrichten {#set-up-a-budget}

1. Öffnen Sie **Observability → Budgets** und wählen Sie **Neues Budget**.
2. Geben Sie einen **Name** ein, z. B. `Vertrieb pro Monat`. Die Benachrichtigungen nennen ihn.
3. Wählen Sie unter **Gilt für** **Organisation**, **Team**, **Agent** oder **Person** und dann das Team, den
   Agenten oder die Person (siehe [Wofür ein Budget gilt](#who-a-budget-applies-to)).
4. Wählen Sie unter **Zählt** **Tokens** oder **Geld**. Geben Sie für Geld die **Währung** ein, z. B. `EUR`.
5. Geben Sie den **Betrag** ein und wählen Sie den **Zeitraum**: **Pro Tag**, **Pro Woche** oder **Pro Monat**.
6. Wählen Sie den **Modus**: **Hart**, **Weich** oder **Schatten** (siehe [Modi](#modes)).
7. Behalten Sie unter **Benachrichtigen bei (%)** `80, 100` oder geben Sie andere Prozentwerte des Betrags ein.
   Wählen Sie eine Person unter **Verantwortlich**, wenn außer der Administration jemand die Benachrichtigungen
   erhalten soll.
8. Wählen Sie **Erstellen**.

Das Budget zählt ab dem Beginn des laufenden Zeitraums. Jede Änderung und jedes neue oder gelöschte Budget wird
mit Ihrem Namen im Audit-Protokoll festgehalten (`budget.create`, `budget.update`, `budget.delete`) und gilt
innerhalb von Sekunden für die Anfragen.

??? info "Mehr zu den Zeiträumen und dazu, was gezählt wird"
    - **Zeiträume** sind Kalendertage, Wochen von Montag bis Sonntag und Kalendermonate, in der Zeitzone der
      Nutzungsmessung (**Einstellungen → Nutzung und Datenschutz**, **Zeitzone**). Ein Budget beginnt um
      Mitternacht neu, wenn sein Zeitraum endet.
    - **Tokens** sind Eingabe- und Ausgabe-Tokens zusammen, solche aus dem Cache eingeschlossen, wie auf den
      Nutzungsseiten (siehe [Was gezählt wird](usage.md#what-is-counted)).
    - **Geld** sind die geschätzten Kosten zu den auf dieser Instanz gesetzten Preisen, in der Währung des Budgets.
      Die Nutzung von Modellen mit Preisen in einer anderen Währung zählt nicht dafür; die Liste sagt das unter dem
      Budget.
    - Eine Anfrage zählt, sobald sie beginnt, mit ihrem Prompt und dem Höchsten, was sie schreiben darf; ist die
      Antwort fertig, treten ihre tatsächlichen Tokens an diese Stelle. Eine Antwort, die bereits geschrieben wird,
      wird nie abgebrochen; ein Budget kann seinen Betrag daher leicht überschreiten.
    - Wofür ein Budget gilt, steht fest, sobald es besteht. Um ein Budget auf ein anderes Team zu legen, löschen Sie
      es und richten ein neues ein.

## Die Liste {#the-list}

Jedes Budget auf dem Reiter **Budgets** zeigt für den laufenden Zeitraum:

| Sie sehen | Was es bedeutet |
|---|---|
| **Team · Vertrieb**, **Agent · …**, **Die ganze Organisation** | Wofür es gilt. |
| **Pro Monat**, **Hart**, **Limit 1.000.000 Tokens** | Der Zeitraum, der Modus und der Betrag. |
| **Dieser Zeitraum: … bis …** | Der erste und der letzte Tag des laufenden Zeitraums. |
| Ein Balken mit **250.000 von 1.000.000 Tokens** und **25 %** | Was bisher verbraucht ist. Gelb ab 80 %, rot, sobald es aufgebraucht ist. |
| **Bei diesem Verbrauch ist es am … aufgebraucht** | Die bisherige Nutzung, im selben Tempo fortgesetzt, würde es vor dem Ende des Zeitraums aufbrauchen. |
| **Hätte in diesem Zeitraum 12 Anfragen abgelehnt.** | Ein Schatten-Budget: was ein hartes Budget abgelehnt hätte. |
| **Benachrichtigung bei 80 %, 100 %**, **auch an …** | Die Schwellen und die verantwortliche Person, die die Benachrichtigungen ebenfalls erhält. |
| **Die Budgets der Teams darunter ergeben zusammen mehr als dieses.** | Erlaubt: siehe [Die Baumregel](#the-tree-rule). |
| **Nur Kosten in EUR zählen.** oder **Kein Modell hat einen Preis in CHF** | Modelle mit Preisen in einer anderen Währung zählen für dieses Budget nicht. |
| **Was eine Person von ihrem Budget nutzt, sieht nur sie selbst.** | Das Budget einer Person: Sie sehen sein Limit, nicht ihre Nutzung (siehe [Datenschutz](#privacy)). |

Die Budgets stehen nach Namen sortiert. Bearbeiten Sie eines mit dem Stift, löschen Sie es mit dem Papierkorb;
ein gelöschtes Budget begrenzt sofort nichts mehr.

## Wofür ein Budget gilt {#who-a-budget-applies-to}

| Gilt für | Welche Anfragen zählen | Wer sieht, was verbraucht ist |
|---|---|---|
| **Organisation** | Jede Anfrage der Organisation, über den Chat, die Agenten und die API. | Administratoren; alle unter **Meine Nutzung**, sobald genug Personen es teilen. |
| **Team** | Die Anfragen aller, deren Nutzungsgruppe das Team oder ein Team darunter ist. | Administratoren; die Personen des Teams unter **Meine Nutzung**, sobald das Team mindestens fünf Personen hat. |
| **Agent** | Die Anfragen über diesen Agenten, egal wer ihn nutzt. | Administratoren. |
| **Person** | Nur die Anfragen dieser Person. Nur, solange Budgets für einzelne Personen eingeschaltet sind. | Allein die Person, unter **Meine Nutzung** (siehe [Datenschutz](#privacy)). |

Ein Agent, den nur eine Person nutzt (ein persönlicher Agent), zählt als ihrer: Ein Budget dafür ist ein Budget
für die Person und erfordert eingeschaltete Budgets für einzelne Personen.

## Die Baumregel {#the-tree-rule}

Budgets folgen dem Teambaum (siehe [Teams als Baum](users.md#teams-in-a-tree)):

- **Jedes Budget, das gilt, muss Platz haben.** Eine Anfrage zählt für das Budget der Organisation, das Budget der
  Nutzungsgruppe der Person und jedes Teams darüber bis ganz oben, das Budget des Agenten und das Budget der
  Person. Ein hartes Budget ohne Platz lehnt die Anfrage ab, auch wenn die anderen Platz haben.
- **Ein Team ohne eigenes Budget** wird von den Budgets der Teams darüber begrenzt.
- **Das Budget eines Teams darf nicht größer sein als das Budget des Teams darüber**, bei derselben Einheit,
  Währung und demselben Zeitraum. Der Dialog sagt dann **Größer als das Budget des Teams darüber …**: Verringern
  Sie es oder erhöhen Sie zuerst jenes Budget.
- **Die Budgets der Teams darunter dürfen zusammen mehr ergeben** als das ihres übergeordneten Teams. Das ist
  erlaubt, denn oft lässt ein Team Platz ungenutzt; das Budget darüber gilt trotzdem für alle, und die Liste
  warnt darunter.
- **Ein Team braucht mindestens fünf Personen** (die Mindestgruppengröße, Unterteams eingerechnet) für ein eigenes
  Budget, außer die personenbezogene Auswertung ist eingeschaltet (siehe
  [Nutzung und Datenschutz](usage-privacy.md#the-settings)). Die Nutzung eines kleineren Teams wäre die Nutzung
  einzelner Personen.
- Die Nutzung zählt für die Teams, wie sie bestanden, als sie stattfand: Nach einer Umorganisation bleibt
  frühere Nutzung dort, wo sie gezählt wurde.

## Modi {#modes}

| Modus | Wenn das Budget aufgebraucht ist | Wofür |
|---|---|---|
| **Hart** | Anfragen werden bis zum Ende des Zeitraums abgelehnt. Eine Antwort, die bereits geschrieben wird, wird fertig. | Ein festes Limit. |
| **Weich** | Nichts wird abgelehnt. Die Benachrichtigungen gehen hinaus, und der Balken bleibt rot. | Kosten im Blick behalten, ohne jemanden aufzuhalten. |
| **Schatten** | Nichts wird abgelehnt. Die Liste zählt, wie viele Anfragen ein hartes Budget abgelehnt hätte. | Einen Betrag erproben, bevor Sie das Budget auf **Hart** stellen. |

### Was Benutzer sehen, wenn ein Budget ablehnt {#what-people-see-when-a-budget-refuses}

Im Chat sehen Benutzer **Budget aufgebraucht** mit einer Meldung in ihrer Sprache, die die Art des Budgets nennt,
etwa „Das würde das Budget Ihres Teams für diesen Zeitraum überschreiten. Wenden Sie sich für mehr an die
Administration oder an die verantwortliche Person des Budgets.“, und sagt, wann es neu beginnt. Ebenso für „Ihr
persönliches Budget“, „das Budget dieses Agenten“ und „das Budget der Organisation“. Sie zeigt nie eine Zahl oder
die Nutzung anderer, und niemand sonst wird benachrichtigt.

Anwendungen und Agenten erhalten die Antwort, die die API von OpenAI für ein Kontingent gibt: HTTP-Status `429`
mit `error.type` `insufficient_quota`, `error.code` `budget_exceeded` und der Art des Budgets in `error.param`
(`personal`, `team`, `agent` oder `organisation`), `Retry-After` mit den Sekunden bis zum Ende des Zeitraums und
`x-should-retry: false`, sodass die Client-Bibliotheken von OpenAI es nicht von selbst erneut versuchen.

## Benachrichtigungen und wer sie erhält {#alerts-and-who-gets-them}

Ein Budget benachrichtigt einmal pro Schwelle und Zeitraum: bei 80 % und 100 %, wenn Sie keine anderen Schwellen
setzen. Einmal mehr pro Zeitraum benachrichtigt es, wenn es beim bisherigen Verbrauch vor dem Ende des Zeitraums
aufgebraucht wäre.

| Budget | Wer benachrichtigt wird, und wie |
|---|---|
| Organisation, Team, Agent | Administratoren, durch einen Hinweis über jeder Seite der Konsole („Das Budget … hat in diesem Zeitraum (Monat) 80 % seines Betrags erreicht.“), und per Mail: an die verantwortliche Person des Budgets, wenn es eine hat, sonst an die Empfänger der Warnungen unter **Einstellungen → Mail**. |
| Person | Allein die Person, unter **Meine Nutzung**, bei **Budgets**. Keine Administration wird benachrichtigt. |

Keine Benachrichtigung enthält die Nutzung einer einzelnen Person. Die Mail setzt voraus, dass Mail unter
**Einstellungen → Mail** eingerichtet ist.

## Verrechnung {#chargeback}

Der Reiter **Verrechnung** zeigt die Nutzung und die geschätzten Kosten eines Monats je Team oder je
Kostenstelle, um Kosten innerhalb der Organisation zu verrechnen.

1. Öffnen Sie **Observability → Budgets** und wählen Sie den Reiter **Verrechnung**.
2. Wählen Sie den **Monat**: Zuerst erscheint der vergangene Monat.
3. Wählen Sie unter **Je** **Team** oder **Kostenstelle**.
4. Wählen Sie **CSV herunterladen**, um denselben Bericht als Datei zu erhalten.

- **Je Team** enthalten die Zahlen eines Teams die seiner Unterteams, und die Zeilen sind nach dem Baum
  eingerückt. Personen ohne Nutzungsgruppe werden unter **Nicht zugeordnet** gezählt.
- **Je Kostenstelle** folgen die Zahlen der **Kostenstelle** jedes Teams, wie unter **Zugriffskontrolle →
  Gruppen** festgelegt. Teams ohne Kostenstelle werden unter **Ohne Kostenstelle** gezählt.
- **Jede Zeile umfasst mindestens fünf Personen.** Kleinere Teams oder Kostenstellen werden in der Zeile
  **Weniger als 5 Personen** zusammen gezählt, wie auf der Seite „Nutzung“ (siehe
  [Die Mindestgruppengröße](usage.md#the-minimum-group-size)).
- **Die Kosten sind eine interne Schätzung**: die Tokens zu den auf dieser Instanz gesetzten Preisen, nicht das,
  was ein Provider Ihnen in Rechnung stellt. Die Seite und die Datei sagen das.

??? info "Die CSV-Datei und FinOps FOCUS"
    Die Datei nutzt die Spaltennamen der FinOps-FOCUS-Spezifikation (Version 1.4), damit Kostenwerkzeuge sie
    lesen können: eine Zeile je Team oder Kostenstelle, Modell und Token-Richtung.

    | Spalte | Enthält |
    |---|---|
    | `BillingPeriodStart`, `BillingPeriodEnd`, `ChargePeriodStart`, `ChargePeriodEnd` | Den Monat. |
    | `ChargeCategory` | `Usage`. |
    | `ServiceCategory`, `ServiceName` | `AI and Machine Learning`, `Fadenstack`. |
    | `SkuId` | Den Namen des Modells. |
    | `ConsumedQuantity`, `ConsumedUnit` | Die Tokens und `Tokens`. |
    | `PricingUnit` | `1000000 Tokens`. |
    | `EffectiveCost`, `BillingCurrency` | Die geschätzten Kosten und ihre Währung. |
    | `x_Team`, `x_CostCenter` | Das Team oder die Kostenstelle. |
    | `x_TokenDirection` | `input` oder `output`. |
    | `x_CostIsEstimate` | `true`: Die Kosten sind eine interne Schätzung. |

## Die Einstellungen {#the-settings}

Öffnen Sie **Observability → Budgets** und wählen Sie den Reiter **Einstellungen**. Jede Änderung wird im
Audit-Protokoll festgehalten.

| Einstellung | Standard | Was eine Änderung bedeutet |
|---|---|---|
| **Personenbudgets** (`faden_ee.budgets.person_budgets`) | Aus | Ein: Die Administration kann einer Person ein Budget geben. Nur diese Person sieht, was sie davon genutzt hat, und nur sie erhält seine Benachrichtigungen. Das Ein- und Ausschalten erfordert den **Root-Modus**. Stimmen Sie es vorher ab: siehe [Datenschutz](#privacy). |
| **Nutzungsgruppe aus der Kostenstelle setzen** (`faden_ee.budgets.cost_center_rule`) | Aus | Ein: Legt Ihr Anmeldeanbieter eine Person durch Provisionierung (SCIM) an oder ändert sie und sendet ihre Kostenstelle, setzt die Regel für diese Kostenstelle ihre Nutzungsgruppe. Personen, deren Kostenstelle keine Regel hat, behalten ihre. |

Wählen Sie unter **Regeln** **Regel hinzufügen**, geben Sie die **Kostenstelle** so ein, wie Ihr Anmeldeanbieter
sie sendet, und wählen Sie das **Team**. Jede Kostenstelle nur einmal. Ändert eine Regel die Nutzungsgruppe einer
Person, wird das im Audit-Protokoll festgehalten. Wie der Anmeldeanbieter die Kostenstelle sendet, steht unter
[Provisionierung (SCIM)](sign-in-provisioning.md).

## Datenschutz {#privacy}

Budgets arbeiten mit Nutzungszahlen, und die sind im Betrieb Daten über Beschäftigte (siehe
[Nutzung und Datenschutz](usage-privacy.md)). Für sie gelten dieselben Regeln:

- **Budgets für einzelne Personen sind standardmäßig ausgeschaltet.** Ein Budget für eine Person begrenzt, wie
  diese Person arbeitet. In Deutschland ist das Einschalten mitbestimmungspflichtig (§ 87 Abs. 1 Nr. 6 BetrVG):
  Stimmen Sie es vorher mit dem Betriebsrat ab und informieren Sie die Beschäftigten in Ihren Hinweisen (siehe
  [Unterlagen für Datenschutz und Betriebsrat](usage-documents.md)).
- **Die Nutzung einer Person gehört ihr allein.** Sie sieht sie unter **Meine Nutzung**. Die Administration sieht
  das Budget einer Person, sein Limit und seinen Modus, aber weder ihre Nutzung noch, in der Liste, wessen Budget
  es ist: außer die personenbezogene Auswertung ist eingeschaltet und sie hat die Rolle `usage_auditor`.
- **Die Benachrichtigungen einer Person gehen nur an diese Person.** Niemand sonst erfährt, wenn sie eine Schwelle
  erreicht.
- **Eine Ablehnung nennt die Art des Budgets, nie eine Zahl.** Niemand sonst wird darüber informiert.
- **Teamzahlen brauchen fünf Personen.** Das Budget eines Teams, seine Nutzung unter **Meine Nutzung** und jede
  Zeile der Verrechnung umfassen mindestens fünf Personen, wie jede Zahl auf der Seite „Nutzung“.
- **Die Verrechnung erfolgt je Team oder Kostenstelle, nie je Person**, und ihr Geld ist eine interne Schätzung.
- **Nichts stellt Rangfolgen auf oder vergleicht**, wie Teams oder Personen ihre Budgets nutzen.

Was für Budgets gespeichert wird, steht unter [Nutzung und Datenschutz](usage-privacy.md#the-data-fields).

## Ihre eigenen Budgets {#your-own-budgets}

Jede Person sieht die Budgets, die für sie gelten, unter **Meine Nutzung** bei **Budgets**: ihr persönliches
Budget, das ihres Teams, das eines Agenten, das der Organisation. Für jedes, was davon verbraucht ist (bei einem
Team oder der Organisation nur, wenn genug Personen es teilen), wann der Zeitraum endet, was geschieht, wenn es
aufgebraucht ist, und die Benachrichtigungen dieses Zeitraums.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **Dieses Team hat weniger als 5 Personen, Unterteams eingerechnet.** | Legen Sie das Budget auf das Team darüber, oder schalten Sie die personenbezogene Auswertung ein, sobald sie abgestimmt ist (siehe [Die Baumregel](#the-tree-rule)). |
| **Größer als das Budget des Teams darüber …** | Verringern Sie den Betrag oder erhöhen Sie zuerst das Budget des übergeordneten Teams. |
| **Budgets für einzelne Personen sind ausgeschaltet.** | Schalten Sie **Personenbudgets** unter **Einstellungen** auf dieser Seite ein, im **Root-Modus**. |
| **Nur eine Person nutzt diesen Agenten, daher ist sein Budget ein Budget für diese Person.** | Das Budget eines persönlichen Agenten ist ein Personenbudget: Schalten Sie zuerst **Personenbudgets** ein, oder legen Sie ein Budget für einen gemeinsam genutzten Agenten an. |
| **Ein Budget hierfür mit derselben Einheit und demselben Zeitraum gibt es bereits.** | Bearbeiten Sie stattdessen jenes Budget. Ein zweites für dasselbe Team braucht eine andere Einheit oder einen anderen Zeitraum. |
| **Das Ein- oder Ausschalten von Budgets für einzelne Personen erfordert den Root-Modus.** | Starten Sie den **Root-Modus** in der oberen Leiste und speichern Sie erneut. |
| **Kein Modell hat einen Preis in …** oder **Nur Kosten in … zählen.** | Legen Sie Preise in der Währung des Budgets fest (siehe [Preise festlegen](usage.md#set-prices)), oder ändern Sie die Währung des Budgets. |
| Ein Team wird abgelehnt, obwohl sein Budget Platz hat | Ein anderes Budget auf dem Weg hat keinen: das der Organisation, eines Teams darüber, des Agenten oder der Person. |
| Ein Budget lehnt nie ab | Es ist **Weich** oder **Schatten**. Bearbeiten Sie es und wählen Sie **Hart**. |
| **Budgets** fehlt unter **Observability** | Budgets erfordern Fadenstack Enterprise mit einer gültigen Lizenz. |
