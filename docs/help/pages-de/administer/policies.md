# Richtlinien und personenbezogene Daten {#policies-and-personal-data}

Sie legen fest, welche personenbezogenen Daten maskiert werden, bevor sie ein Modell erreichen, und welche
Tools ohne Rückfrage laufen dürfen. Beides gilt für jeden Chat und jeden Agenten. Diese Seite behandelt die
Richtlinie für personenbezogene Daten, die Tool-Regeln und das, was Benutzer sehen, wenn eine Regel etwas stoppt.

## Bevor Sie beginnen {#before-you-start}

- Für personenbezogene Daten: Das Modul **PII / Datenschutz** ist eingeschaltet (**Einstellungen → Module**; siehe
  [Module](index.md#modules)). Seine Seiten finden Sie unter **Datenschutz (PII)**. Ohne das Modul wird nichts maskiert.
- Für Tool-Regeln: **Agenten → Governance**. Dafür ist kein Modul nötig.

## Personenbezogene Daten {#personal-data}

Der Filter für personenbezogene Daten findet Namen, E-Mail-Adressen, Telefonnummern, Konto- und Kartennummern,
Orte und ähnliche Daten in dem, was Benutzer senden, und maskiert sie, bevor die Anfrage ein Modell erreicht. Er
arbeitet nur so, wie Ihre Richtlinie es vorgibt: Sie wählen, welche Daten, für welche Modelle und was geschieht,
wenn der Filter nicht laufen kann.

!!! note "Die Erkennung ist nicht immer richtig"
    Wie gut der Filter arbeitet, hängt von der Sprache und vom Aufbau der Inhalte ab: Am besten erkennt er ganze
    englische Sätze, weniger gut andere Sprachen sowie Tabellen, Listen, Code und kurze Fragmente. Er kann
    personenbezogene Daten übersehen und Wörter maskieren, die keine sind. Testen Sie ihn mit typischen Inhalten Ihrer
    Organisation, bevor Sie sich darauf verlassen, und weisen Sie Ihre Benutzer darauf hin, nichts einzufügen, was sie
    nicht weitergeben dürfen.

### Die Richtlinie, mit der Fadenstack startet {#the-policy-fadenstack-starts-with}

Bei eingeschaltetem Modul gilt diese Standardrichtlinie:

- Sie schützt Anfragen an **Cloud-Provider**; Anfragen an die Modelle Ihrer eigenen Maschinen werden nicht geprüft.
- Sie **schwärzt**: Personenbezogene Daten werden ersetzt, und das Modell sieht sie nie.
- Sie **blockiert** eine Anfrage an einen Cloud-Provider, wenn der Filter nicht laufen kann.
- Sie liest englischen Text und sucht nach diesen Typen: `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `CREDIT_CARD`,
  `IBAN_CODE`, `IP_ADDRESS`, `US_SSN`, `LOCATION`, `NRP`, `MEDICAL_LICENSE`, `URL`.

??? info "Mehr zu Datumsangaben"
    Datumsangaben (`DATE_TIME`) stehen nicht auf der Liste. Fügen Sie sie hinzu, wenn auch Geburtsdaten erfasst
    werden sollen; einfache Zahlen in einer Nachricht können dann ebenfalls als Datum maskiert werden.

Der Filter hat nur ein englisches Sprachmodell. In deutschem oder anderssprachigem Text findet er weiterhin, was eine
feste Form hat (E-Mail-Adressen, Telefon-, Karten- und Kontonummern, IP-Adressen, Links), übersieht aber oft Namen
und Orte.

### Die Richtlinie ändern {#change-the-policy}

Die **Standardrichtlinie** gilt für die gesamte Installation:

1. Öffnen Sie **Einstellungen → Module**, suchen Sie **PII / Datenschutz** und öffnen Sie dort die **Moduleinstellungen**.
2. Ändern Sie die **Default PII Policy** (Felder siehe unten).
3. Wählen Sie **Speichern**.

!!! warning
    Wenn Sie die Standardrichtlinie speichern, startet das Gateway neu, um sie anzuwenden. Antworten, die in diesem
    Moment geschrieben werden, brechen ab und können erneut versucht werden. Speichern Sie außerhalb der
    Hauptnutzungszeiten.

Eine **Mandantenrichtlinie** ersetzt die Standardrichtlinie für einen Mandanten (Ihre Organisation ist der Mandant
`default`, sofern Ihre Installation keine weiteren nutzt):

![Datenschutz (PII) → Mandantenrichtlinien: das Feld „Mandanten-ID“ und „Mandanten laden“](../images/administer/pii-policies.png)

1. Öffnen Sie **Datenschutz (PII) → Mandantenrichtlinien**, geben Sie die **Mandanten-ID** ein und wählen Sie
   **Mandanten laden**.

    ![Eine Mandantenrichtlinie: Erkennung (Presidio) mit Sprache, Schwellenwert und Entitäten, Override löschen und Speichern, der Schalter „Chats und Agenten dürfen den PII-Modus ändern“ und die Vorschau der effektiven Richtlinie](../images/administer/pii-tenant-policy.png)

2. Ändern Sie die Felder unter **Mandanten-Override**. Die **Vorschau der effektiven Richtlinie** zeigt, was für
   Anfragen gelten wird.
3. Wählen Sie **Speichern**. **Override löschen** kehrt zur Standardrichtlinie zurück.

??? info "Mehr zu den Feldern (an beiden Stellen dieselben)"
    Die Felder sind an beiden Stellen dieselben:

    | Feld | Was es bewirkt |
    |---|---|
    | **Ausgehende Anfragen**: **Anfragen an Cloud-Anbieter schützen**, **Anfragen an lokale Modelle schützen** | Welche Anfragen geprüft werden. |
    | **Modus** | `redact`: Personenbezogene Daten werden durch einen Platzhalter wie `<PERSON>` ersetzt, und die Antwort behält ihn. `tokenize_reversible`: Jeder Wert wird durch ein Token wie `[PERSON_1]` ersetzt und in der Antwort, die der Benutzer sieht, wieder eingesetzt. Das Modell sieht die Daten in keinem der beiden Fälle. |
    | **Wenn die Erkennung fehlschlägt** | `block`: Die Anfrage wird abgelehnt. `allow`: Sie wird ungeprüft gesendet. `fallback_to_local`: Sie wird ungeprüft an eines Ihrer eigenen Modelle mit demselben Namen gesendet, falls es eines gibt, und sonst abgelehnt. |
    | **Erkennung (Presidio)**: **Sprache**, **Schwellenwert**, **Entitäten** | Welche Sprache der Filter liest, wie sicher er sein muss und nach welchen Arten von Daten er sucht. Ein höherer Schwellenwert maskiert weniger. Installiert ist nur Englisch: Lassen Sie **Sprache** auf `en`. Mit einer anderen Sprache schlägt jede Prüfung fehl, und **Wenn die Erkennung fehlschlägt** entscheidet. |
    | **Telemetrie**: **Personenbezogene Daten aus der Telemetrie entfernen**, **Modus** | Für Aufzeichnungen von Anfragen (Traces), die standardmäßig ausgeschaltet sind: ob ein Trace die Anfrage ohne personenbezogene Daten enthält (`sanitized`) oder nur Kennzahlen (`metrics_only`). Ohne Enterprise enthalten Traces ohnehin nur Kennzahlen. |

### Chats und Agenten {#chats-and-agents}

![Der Reiter „Datenschutz“ im Seitenbereich des Chats: Schutz der Anfragen an die Cloud und an lokale Modelle, Modus und Fehleraktion, Telemetrie sowie Schwellenwert und Entitäten der Erkennung](../images/use/chat-privacy-tab.png){ width="280" }

- Ein Benutzer kann die Richtlinie für einen einzelnen Chat im Bereich **Datenschutz** des Chats **strenger**
  machen: mehr Typen, ein niedrigerer Schwellenwert (er maskiert dann mehr), auch lokale Modelle prüfen.
- Der Wechsel zwischen `redact` und `tokenize_reversible` ist keine strengere, sondern eine andere Einstellung.
  Ein Chat darf ihn deshalb nur vornehmen, wenn **Chats und Agenten dürfen den PII-Modus ändern** eingeschaltet ist
  (**Datenschutz (PII) → Mandantenrichtlinien**, standardmäßig aus).
  Andernfalls zeigt der Chat „Ihr Administrator hat nicht erlaubt, den PII-Modus für diesen Chat zu ändern.“
- Die Einstellung **Datenschutz** eines Agenten (**PII-Modus ändern** auf seinem Reiter **Definition**) hat für
  die Unterhaltungen des Agenten Vorrang vor diesem Schalter: **Wie die PII-Richtlinie des Mandanten sagt**,
  **Erlaubt** oder **Nicht erlaubt**.
- Was ein MCP-Tool erhält, legen Sie pro Server fest: siehe
  [MCP-Server](mcp-servers.md#personal-data-pii-mode).

### Was der Filter gefunden hat {#what-the-filter-found}

**Datenschutz (PII) → Dashboard** zeigt nur Zahlen: die Prüfungen, wie viele davon personenbezogene Daten
gefunden haben, die gefundenen Arten und das tägliche Volumen. **Datenschutz (PII) → Aktivitätsprotokoll** listet
jede Prüfung mit Modus, Arten und Anzahl auf, ohne den Text selbst.

## Tool-Regeln {#tool-rules}

![Agenten → Governance auf dem Reiter „Tool-Regeln“, neben Tool-Klassen und Entscheidungen: noch keine Regeln, und „Regel hinzufügen“](../images/administer/governance.png)

**Agenten → Governance → Tool-Regeln** legt nach Namen fest, was geschieht, wenn ein Modell ein Tool ausführen will:

1. Wählen Sie **Regel hinzufügen**.

    ![Neue Tool-Regel: Name, Tool-Muster, Aktion auf Sperren, Nachricht an den Nutzer, Kategorie, Priorität 100 und Aktiv](../images/administer/governance-add-rule.png)

2. Geben Sie unter **Name** eine Bezeichnung (für Sie) und unter **Tool-Muster** einen Tool-Namen oder ein
   Muster mit `*` ein, z. B. `mcp.github.*`, `knowledge_search` oder `*`.
3. Wählen Sie die **Aktion**: **Sperren**, **Erst nachfragen** (der Benutzer gibt jeden Aufruf frei) oder **Erlauben**.
4. Schreiben Sie die **Nachricht an den Nutzer**: Das wird Benutzern mitgeteilt, wenn die Regel greift.
5. Legen Sie die **Priorität** fest. Niedrigere Werte werden zuerst geprüft, und die erste passende Regel
   entscheidet. So kann eine Regel mit einer niedrigeren Zahl eine Ausnahme von einer allgemeineren Regel bilden.
6. Speichern Sie die Regel. Regeln greifen innerhalb von 30 Sekunden.

Ohne Regeln laufen Tools so, wie es der Tool-Modus vorgibt.

### Tool-Klassen und Tool-Modi {#tool-classes-and-tool-modes}

![Agenten → Governance auf dem Reiter „Tool-Klassen“: noch keine Klassen, daher ist jedes Server-Tool nicht klassifiziert, und „Klasse hinzufügen“](../images/administer/governance-classes.png)

**Agenten → Governance → Tool-Klassen** legt nach Muster fest, was Tools tun: **lesen**, **schreiben**,
**zerstörend** oder **Wie sein MCP-Server angibt** (dem vertrauen, was der MCP-Server des Tools über das Tool
angibt). **Wirkt nach außen** hält fest, ob ein Tool über die Organisation hinaus wirkt. Ein Tool, das niemand
einstuft, ist nicht eingestuft.

Benutzer wählen pro Chat einen Tool-Modus, und die Klasse entscheidet, was läuft:

| Modus | lesen | schreiben | zerstörend | nicht eingestuft |
|---|---|---|---|---|
| **Aus** | nicht angeboten | nicht angeboten | nicht angeboten | nicht angeboten |
| **Nur lesen** | läuft | nicht angeboten | nicht angeboten | nicht angeboten |
| **Fragen** (damit beginnt jeder Chat) | läuft | fragt nach | fragt nach | fragt nach |
| **Automatisch** | läuft | läuft | fragt nach | fragt nach |

Kein Modus führt ein zerstörendes oder nicht eingestuftes Tool aus, ohne nachzufragen. Damit ein Tool in
**Automatisch** läuft, stufen Sie es als **lesen** oder **schreiben** ein.
Die eigenen Tools von Fadenstack (die Suche in der Wissensdatenbank, die Uhrzeit, die Tool-Suche) sind lesende Tools.

### Entscheidungen {#decisions}

![Agenten → Governance auf dem Reiter „Entscheidungen“: Filter für Phase, Ergebnis und Agent sowie drei Tool-Entscheidungen, Freigabe nötig, Abgelaufen und Freigegeben, jeweils mit dem, was der Nutzer erfuhr](../images/administer/governance-decisions-top.png)

**Agenten → Governance → Entscheidungen** listet auf, was die Prüfungen entschieden haben: wann, in welcher Phase
(**Eingabe**, **Antwort**, **Tool**), das Ergebnis (**Gesperrt**, **Freigabe nötig**, **Freigegeben**,
**Abgelehnt**, **Abgelaufen**), was dem Benutzer mitgeteilt wurde, den Benutzer, den Agenten und die Anfrage. Der
abgelehnte Text selbst wird nie gespeichert, nur ein mit einem Schlüssel gebildeter Fingerabdruck: Derselbe Text,
zweimal abgelehnt, zeigt denselben Wert, und niemand kann den Text daraus zurückgewinnen.

## Was Benutzer sehen, wenn etwas gestoppt wird {#what-people-see-when-something-is-stopped}

| Was geschehen ist | Im Chat |
|---|---|
| Eine Tool-Regel hat ein Tool gesperrt | Die Tool-Zeile zeigt **Nicht ausgeführt**. Das Modell erfährt es, mit der Nachricht der Regel, und macht ohne das Tool weiter. |
| Ein Tool braucht eine Freigabe | „… möchte ausgeführt werden“, mit **Zulassen** und **Ablehnen**. Kommt innerhalb von fünf Minuten keine Antwort, wird der Aufruf nicht ausgeführt (**Abgelaufen**). |
| Der Filter für personenbezogene Daten konnte nicht laufen, und die Richtlinie blockiert | Die Antwort schlägt fehl mit „PII service unavailable and fail_action=block.“ An das Modell wurde nichts gesendet. |
| Eine Eingabe wurde abgelehnt (Inhaltsrichtlinien, <span class="fs-planned">Geplant</span>) | Die Nachricht wird als **Nicht gesendet** markiert, mit der Nachricht der Richtlinie oder „Dies wurde durch eine Richtlinie Ihrer Organisation gestoppt.“ Nichts wird an ein Modell gesendet oder gespeichert. |
| Eine Antwort wurde abgelehnt (Inhaltsrichtlinien, <span class="fs-planned">Geplant</span>) | Die Antwort bricht ab und wird als **Antwort zurückgehalten** markiert. |

Anwendungen, die die API nutzen, erhalten dieselben Ergebnisse als Fehler, die sie behandeln können; die
Einzelheiten stehen auf der Website [Developers](https://developers.fadenstack.com/).

## Was Core enthält und was geplant ist {#what-core-includes-and-what-is-planned}

| Prüfung | Core | Enterprise |
|---|---|---|
| Wer sich anmelden und einen Agenten nutzen darf | Konten, Rollen, Zugriff auf Agenten | Single Sign-on <span class="fs-planned">Geplant</span> |
| Personenbezogene Daten | PII-Richtlinien, Einstellungen pro Chat und pro Agent | |
| Die Eingabe | | Inhaltsrichtlinien für die Eingabe <span class="fs-planned">Geplant</span> |
| Die Antwort | | Inhaltsrichtlinien für die Antwort <span class="fs-planned">Geplant</span> |
| Tools | Tool-Regeln, Tool-Klassen, Tool-Modi | Inhaltsrichtlinien für Tool-Argumente und Grenzen für den Tool-Modus, den Benutzer wählen dürfen, pro Team, Agent oder Person <span class="fs-planned">Geplant</span> |
| Die Aufzeichnung | Entscheidungen, ohne den Text | Eine Ansicht für Prüfer mit den Namen der Richtlinien sowie Exporte <span class="fs-planned">Geplant</span> |

Inhaltsrichtlinien werden eine Eingabe, eine Antwort oder einen Tool-Aufruf anhand ihres Inhalts ablehnen.
