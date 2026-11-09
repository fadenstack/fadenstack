# Richtlinien und personenbezogene Daten {#policies-and-personal-data}

Sie legen fest, welche personenbezogenen Daten maskiert werden, bevor sie ein Modell erreichen, und welche
Tools ohne Rückfrage laufen dürfen. Beides gilt für jeden Chat und jeden Agenten. Diese Seite behandelt die
Richtlinie für personenbezogene Daten, die Tool-Regeln und das, was Benutzer sehen, wenn eine Regel etwas stoppt.

## Bevor Sie beginnen {#before-you-start}

- Für personenbezogene Daten: Das Modul **PII / Datenschutz** ist eingeschaltet (**Einstellungen → Module**; siehe
  [Module](index.md#modules)). Seine Seiten finden Sie unter **Datenschutz (PII)**. Ohne das Modul wird nichts maskiert, mit einer
  Ausnahme: siehe [Wenn das Modul aus ist](#when-the-module-is-off).
- Für Tool-Regeln: **Agenten → Governance**. Dafür ist kein Modul nötig.

## Personenbezogene Daten {#personal-data}

Der Filter für personenbezogene Daten findet Namen, E-Mail-Adressen, Telefonnummern, Konto- und Kartennummern,
Orte und ähnliche Daten in dem, was Benutzer senden, und maskiert sie, bevor die Anfrage ein Modell erreicht. Er
arbeitet nur so, wie Ihre Richtlinie es vorgibt: Sie wählen, welche Daten, für welche Modelle und was geschieht,
wenn der Filter nicht laufen kann.

!!! note "Die Erkennung ist nicht immer richtig"
    Wie gut der Filter arbeitet, hängt vom installierten Profil, von der Sprache und vom Aufbau der Inhalte ab.
    Tabellen, Listen, Code und kurze Fragmente brauchen besondere Aufmerksamkeit. Er kann
    personenbezogene Daten übersehen und Wörter maskieren, die keine sind. Testen Sie ihn mit typischen Inhalten Ihrer
    Organisation, bevor Sie sich darauf verlassen, und weisen Sie Ihre Benutzer darauf hin, nichts einzufügen, was sie
    nicht weitergeben dürfen.

### Die Richtlinie, mit der Fadenstack startet {#the-policy-fadenstack-starts-with}

Bei eingeschaltetem Modul gilt diese Standardrichtlinie:

- Sie schützt Anfragen an **Cloud-Provider**; Anfragen an die Modelle Ihrer eigenen Maschinen werden nicht geprüft.
- Sie **schwärzt**: Personenbezogene Daten werden ersetzt, und das Modell sieht sie nie.
- Sie **blockiert** eine Anfrage an einen Cloud-Provider, wenn der Filter nicht laufen kann.
- Sie liest englischen Text und nennt keine eigenen Typen: Sie sucht nach den Standardtypen, die die installierte
  Erkennung findet, mit deren Schwellenwerten. Das sind `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `CREDIT_CARD`,
  `IBAN_CODE`, `IP_ADDRESS`, `US_SSN`, `LOCATION`, `MEDICAL_LICENSE` und `URL`, mit dem älteren Profil
  `presidio-legacy` außerdem `NRP` (Nationalität, Religion, politische Gruppe), das die lokalen Erkennungen nicht
  finden. Das Richtlinienformular zeigt sie unter **Entitäten**, solange keine gewählt sind.

Vor 0.6.0 nannte die Standardrichtlinie selbst elf Typen, darunter `NRP`, mit einem Schwellenwert für das ältere
Profil. Die lokalen Erkennungen lehnen beides ab; mit einer davon wurde deshalb jede Anfrage an einen Cloud-Provider
abgelehnt. Eine Installation, die diese Standardrichtlinie noch so hat, wie sie eingerichtet wurde, erhält beim Start
die neue; eine Standardrichtlinie, die Sie geändert haben, bleibt, wie Sie sie gespeichert haben (siehe
[Ein installiertes Erkennungsprofil übernehmen](#adopt-an-installed-detector-profile)).

### Wenn das Modul aus ist {#when-the-module-is-off}

Eine Richtlinie braucht das Modul, um zu laufen. Was ohne es geschieht, hängt davon ab, woher die Richtlinie kommt:

- **Eine für Ihre Organisation gespeicherte Richtlinie** (**Einstellungen → Module → PII / Datenschutz**,
  Mandantenrichtlinie): Ein fehlendes Modul gilt als Filter, der nicht laufen kann, und **Wenn die Erkennung fehlschlägt**
  entscheidet. Bei `block`, dem Standard, schlagen Chats an die Modelle, die die Richtlinie erfasst,
  fehl mit „A PII policy is set for this workspace, but the PII module is not running“, und es wird nichts
  gesendet. Starten Sie das Modul, oder schalten Sie die Richtlinie aus, wenn Sie sie nicht brauchen. Bei `allow`
  geht die Anfrage ungeprüft hinaus, und das Protokoll vermerkt es.
- **Nur die Standardrichtlinie** (niemand hat eine Richtlinie gespeichert): wird nicht angewendet. Jede
  Installation hat sie, auch die, die das Modul nie eingeschaltet haben, und deren Chats laufen ohne Maskierung
  weiter.

Das Ausschalten des Moduls entfernt eine gespeicherte Richtlinie also nicht stillschweigend. Beachten Sie: Wer sich
auf die Standardrichtlinie verlässt und das Modul später ausschaltet, dessen Anfragen an Cloud-Provider werden
nicht mehr maskiert. Speichern Sie eine Richtlinie für Ihre Organisation, wenn das nicht passieren darf.

Solange das so ist, sehen Administratoren es oben auf jeder Verwaltungsseite, als Fehler, der sich nicht
ausblenden lässt: *„PII ist aus: Anfragen an Cloud-Modelle gehen mit unmaskierten personenbezogenen Daten
hinaus.“* Der Hinweis erscheint, sobald das zuvor eingeschaltete Modul aus ist, ein Cloud-Modell aktiviert ist und
keine Richtlinie für Ihre Organisation gespeichert ist; er verschwindet, wenn das Modul wieder an ist
(`faden module enable pii`), kein Cloud-Modell mehr aktiviert ist oder die Standardrichtlinie Cloud-Modelle nicht
mehr prüft (dann ist das unmaskierte Senden Ihre Entscheidung).

??? info "Mehr zu Datumsangaben"
    Datumsangaben (`DATE_TIME`) stehen nicht auf der Liste. Fügen Sie sie hinzu, wenn auch Geburtsdaten erfasst
    werden sollen; einfache Zahlen in einer Nachricht können dann ebenfalls als Datum maskiert werden.

Das ältere Profil `presidio-legacy` unterstützt Englisch. Die lokalen mehrsprachigen Profile unterstützen Englisch
(`en`), Deutsch (`de`), Französisch (`fr`) und Spanisch (`es`). Das Formular zeigt die Sprachen und Kategorien des
laufenden Dienstes. Eine erfolgreiche Prüfung bestätigt keinen Schutz für eine nicht aufgeführte Sprache.

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
    | **Erkennung (Presidio)**: **Sprache**, **Schwellenwert**, **Entitäten** | Das installierte Profil, die Sprache und die Kategorien. **Schwellenwerte des Profils verwenden** übernimmt die Vorgaben des Profils. Ein höherer manueller Schwellenwert maskiert weniger. **Entitäten** leer gelassen: die Standardtypen, die die installierte Erkennung findet. Wählen Sie nur angebotene Sprachen und verfügbare Kategorien; andernfalls schlägt die Prüfung fehl und **Wenn die Erkennung fehlschlägt** entscheidet. |
    | **Telemetrie**: **Personenbezogene Daten aus der Telemetrie entfernen**, **Modus** | Für Aufzeichnungen von Anfragen (Traces), die standardmäßig ausgeschaltet sind: ob ein Trace die Anfrage ohne personenbezogene Daten enthält (`sanitized`) oder nur Kennzahlen (`metrics_only`). Ohne Enterprise enthalten Traces ohnehin nur Kennzahlen. |

### Ein installiertes Erkennungsprofil übernehmen {#adopt-an-installed-detector-profile}

**Installierte Erkennung** zeigt Modell, Inhaltssprachpaket und Ausführungsmodus. Angeboten werden nur
die installierten Sprachen des Pakets. Prüfen Sie nach einem Paketwechsel jede betroffene Richtlinie.

Wenn Ihr Betreiber die Erkennung oder den Filtersatz ändert, prüfen Sie die Standardrichtlinie und die
Mandanten-Overrides, die das Profil verwenden sollen:

1. Prüfen Sie, ob das installierte Profil verfügbar ist. Eine Meldung über einen nicht verfügbaren Dienst bestätigt
   keinen aktiven Schutz.
2. Wählen Sie unter **Erkennung (Presidio)** die Schaltfläche **Standardwerte des installierten Profils verwenden**.
   Dadurch werden das installierte Profil und **Schwellenwerte des Profils verwenden** gewählt, und Kategorien, die
   die installierte Erkennung nicht findet, werden entfernt. Ihre übrigen Kategorien und Einstellungen für ausgehende
   Anfragen bleiben erhalten. Das Formular warnt, wenn der Schwellenwert oder eine Kategorie einer Richtlinie nicht zur
   installierten Erkennung passt: Bis das Profil übernommen ist, werden die Anfragen, die die Richtlinie erfasst,
   abgelehnt.
3. Wählen Sie **Sprache**. Entfernen Sie Kategorien, die dafür nicht verfügbar sind, und ergänzen Sie unter
   **Entitäten** die benötigten Kategorien. Das Übernehmen des Profils wählt nicht automatisch alle Kategorien aus.
4. Wählen Sie den Schutz für Cloud-Anfragen, lokale Modelle oder beides. Lassen Sie **Wenn die Erkennung fehlschlägt**
   auf `block`, wenn eine geschützte Anfrage nur nach erfolgreicher Prüfung gesendet werden darf. Speichern Sie.

Ein Sprachwechsel kann eine zuvor gewählte Kategorie ungültig machen. Entfernen oder ersetzen Sie diese
ausdrücklich. Der Dienst lehnt nicht unterstützte Kategorien ab, statt sie stillschweigend zu ignorieren.

??? info "Erweiterte Kategorien und ihre Grenzen"
    Wenn Ihr Betreiber den [erweiterten Filtersatz](../operate/installing.md#extended-pii-fields) aktiviert, stehen
    unter anderem Datum, Alter, Geschlecht, Postleitzahlen, Identitätsdokumente, Steuer- und
    Sozialversicherungsnummern sowie ausdrücklich benannte sensible Felder zur Verfügung. Wählen Sie die benötigten
    Kategorien in der Richtlinie; die Verfügbarkeit allein aktiviert sie nicht.

    Gesundheitsdaten, ethnische Herkunft, politische Meinungen, Religion oder Weltanschauung,
    Gewerkschaftszugehörigkeit, genetische und biometrische Daten, Sexualleben, sexuelle Orientierung und
    strafrechtliche Verurteilungen werden in unterstützten ausdrücklich benannten Feldern erkannt. Das ist keine
    allgemeine Erkennung jeder sensiblen Aussage in Gesprächen und keine Garantie für DSGVO-Konformität.
    Testen Sie die Inhalte und Feldformate Ihrer Organisation.

    Numerische Werte oder Container unter einem geschützten sensiblen Schlüssel in Tool-Argumenten können
    abgelehnt werden, wenn eine Ersetzung den Datentyp ändern würde. Andere Werte behalten ihre Typen. Bilder und
    Audio sind keine Textprüfungen; für nicht unterstützte geschützte Inhalte gilt **Wenn die Erkennung fehlschlägt**.

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

Das Dashboard zeigt auch **Ø Verarbeitungszeit** und **p95 Verarbeitungszeit**, das Aktivitätsprotokoll die
**Dauer**. Gemeint ist die gemeldete Verarbeitungszeit des PII-Dienstes einschließlich des Wartens auf die
Erkennung. Die Antwortzeit des Sprachmodells gehört nicht dazu. Ältere Ereignisse können ohne Dauer erscheinen.
Die Erkennungsrate ist der Anteil der Prüfungen mit einem Fund, nicht der Anteil erfolgreich entfernter Daten.

## Tool-Regeln {#tool-rules}

![Agenten → Governance auf dem Reiter „Tool-Regeln“, neben Tool-Klassen und Entscheidungen: noch keine Regeln, und „Regel hinzufügen“](../images/administer/governance.png)

**Agenten → Governance → Tool-Regeln** legt nach Namen fest, was geschieht, wenn ein Modell ein Tool ausführen will:

1. Wählen Sie **Regel hinzufügen**.

    ![Neue Tool-Regel: Name, Tool-Muster, Aktion auf Sperren, Nachricht an den Nutzer, Kategorie, Priorität 100 und Aktiv](../images/administer/governance-add-rule.png)

2. Geben Sie unter **Name** eine Bezeichnung (für Sie) und unter **Tool-Muster** einen Tool-Namen oder ein
   Muster mit `*` ein, z. B. `mcp.github.*`, `knowledge_search` oder `*`.
3. Wählen Sie die **Aktion**: **Sperren** oder **Erst nachfragen** (der Benutzer gibt jeden Aufruf frei).
   Eine Regel gilt auch für die Tools, die die App eines Agenten selbst ausführt, etwa die Tools des Browser- und
   der Office-Agenten: Die App fragt jedes Mal, egal was der Benutzer vorher erlaubt hat, und einer App, die nicht
   fragen kann, wird das Tool nicht angeboten.
4. Schreiben Sie die **Nachricht an den Nutzer**: Das wird Benutzern mitgeteilt, wenn die Regel greift.
5. Legen Sie die **Priorität** fest. Sie ordnet Regeln mit derselben Aktion: Passen mehrere davon, liefert die mit
   der niedrigsten Zahl die Nachricht.
6. Speichern Sie die Regel. Regeln greifen sofort.

Ohne Regeln laufen Tools so, wie es der Tool-Modus vorgibt.

### Wenn mehrere Regeln passen {#when-several-rules-match}

**Sperren** gewinnt vor **Erst nachfragen**, unabhängig von der Priorität. Um einige Tools von einer Regel
**Sperren** auszunehmen, fassen Sie deren Muster enger: Sperren Sie `mcp.github.delete_*` statt `mcp.github.*`.
Soll ein Tool ohne Nachfrage laufen, geben Sie ihm eine Tool-Klasse (siehe
[Tool-Klassen und Tool-Modi](#tool-classes-and-tool-modes)).

### Regeln „Erlauben“ aus früheren Versionen {#allow-rules}

Frühere Versionen boten auch **Erlauben** an. Es konnte keine Ausnahme von einer Regel **Sperren** oder **Erst
nachfragen** bilden und änderte für sich allein nichts; neue Regeln können deshalb nicht mehr erlauben. Damit
angelegte Regeln stehen weiter in der Liste, markiert mit **Ohne Wirkung**: Ändern Sie sie auf **Sperren** oder
**Erst nachfragen**, oder löschen Sie sie.

### Tool-Klassen und Tool-Modi {#tool-classes-and-tool-modes}

![Agenten → Governance auf dem Reiter „Tool-Klassen“: noch keine Klassen, daher ist jedes Server-Tool nicht klassifiziert, und „Klasse hinzufügen“](../images/administer/governance-classes.png)

**Agenten → Governance → Tool-Klassen** legt nach Muster fest, was Tools tun: **lesen**, **schreiben**,
**zerstörend** oder **Wie sein MCP-Server angibt** (dem vertrauen, was der MCP-Server des Tools über das Tool
angibt). **Wirkt nach außen** hält fest, ob ein Tool über die Organisation hinaus wirkt. Ein Tool, das niemand
einstuft, ist nicht eingestuft. Die eigenen Tools eines Agenten bekommen ihre Klasse aus seinen **Host-Tools**;
bei einem Agenten, dessen Version keine nennt, ist jedes Tool nicht eingestuft.

Benutzer wählen pro Chat einen Tool-Modus, und die Klasse entscheidet, was läuft:

| Modus | lesen | schreiben | zerstörend | nicht eingestuft |
|---|---|---|---|---|
| **Aus** | nicht angeboten | nicht angeboten | nicht angeboten | nicht angeboten |
| **Nur lesen** | läuft | nicht angeboten | nicht angeboten | nicht angeboten |
| **Fragen** (damit beginnt jeder Chat) | läuft | fragt nach | fragt nach | fragt nach |
| **Automatisch** | läuft | läuft | fragt nach | fragt nach |

Kein Modus bis **Automatisch** führt ein zerstörendes oder nicht eingestuftes Tool aus, ohne nachzufragen. Damit ein Tool in
**Automatisch** läuft, stufen Sie es als **lesen** oder **schreiben** ein. Ein fünfter Modus, [Bypass](#bypass), ist aus, solange Sie ihn nicht erlauben.
Die eigenen Tools von Fadenstack (die Suche in der Wissensdatenbank, die Uhrzeit, die Tool-Suche) sind lesende Tools.

### Grenzen für den Tool-Modus {#tool-mode-limits}

<span class="fs-enterprise" title="Erfordert Fadenstack Enterprise">Enterprise</span>

Mit Enterprise legt **Agenten → Governance → Grenzen für den Tool-Modus** den höchsten Tool-Modus fest, den ein
Chat haben darf, für **Alle**, **Ein Team**, **Einen Agenten** oder **Eine Person**: Wählen Sie unter **Für**, für
wen, wählen Sie das Team, den Agenten oder die Person, dann **Höchstens**, und wählen Sie **Grenze setzen**.
Ändern Sie eine Grenze in ihrer Zeile, oder entfernen Sie sie. Für einen Chat können mehrere Grenzen gelten (die
für alle, die der Teams der Person, die des Agenten, die eigene der Person): Die engste gilt. Benutzer sehen, dass
ihr Chat begrenzt ist, und die Anwendungen eines Agenten erhalten seine Grenze ebenfalls. Änderungen wirken
sofort und werden im Audit-Protokoll festgehalten.

Ohne gültige Enterprise-Lizenz können die Grenzen nicht angewendet werden, daher sind Tools in jedem Chat nur
lesend, bis die Lizenz zurück ist; ein Hinweis für Administratoren sagt das. **Aufheben** in diesem Hinweis gibt
jedem Chat wieder den Modus, den jede Person wählt; die Grenzen bleiben gespeichert und gelten wieder, sobald die
Lizenz gültig ist.

### Freigabe-Grenzen {#approval-limits}

<span class="fs-enterprise" title="Erfordert Fadenstack Enterprise">Enterprise</span>

Mit Enterprise legt **Agenten → Governance → Freigabe-Grenzen** für **Alle**, **Ein Team**, **Einen Agenten**
oder **Eine Person** fest, wie lange eine [Freigabe auf Zeit](#running-grants) dauern darf und was der Server von
den Aufrufen festhält, die sie abdeckt. Wählen Sie unter **Für**, für wen, wählen Sie das Team, den Agenten oder
die Person, geben Sie eine **Längste Freigabe (Minuten)** (15 bis 720), eine **Nachweisstufe** oder beides an, und
wählen Sie **Grenze setzen**. Für eine Freigabe können mehrere Grenzen gelten: Die kürzeste längste Freigabe und
die Stufe, die am meisten festhält, gelten. Die eigene längste Freigabe des Agenten und **Longest grant (minutes)**
unter **Einstellungen → Freigaben** gelten weiter; die kürzeste von allen zählt.

| Nachweisstufe | Was der Server von jedem abgedeckten Aufruf festhält |
|---|---|
| **Entscheidungen** | Das Tool, die Zeit, welche Seite es ausführte und welche Freigabe es abdeckte |
| **Aufrufe** | Dazu seinen Ort und seine Argumente, nachdem personenbezogene Daten maskiert wurden |
| **Ergebnisse** | Dazu, ob er gelang: den Fehler oder die ersten 500 Zeichen seines Ergebnisses, nachdem personenbezogene Daten maskiert wurden |

Ohne Grenze halten Freigaben für eine Zeit **Entscheidungen** fest, gemeinsames Arbeiten und Bypass **Aufrufe**.
Eine Stufe, die Sie setzen, ersetzt das, auch für Bypass. Orte, Argumente und Ergebnisse liegen verschlüsselt auf
dem Server.

Eine Freigabe erhält ihre längste Dauer und ihre Stufe, wenn sie entsteht. Eine **kürzere** Grenze wirkt sofort:
Laufende Freigaben, die sie abdeckt und die länger dauern würden, enden bei ihrem Beginn plus der neuen Grenze, und
jede solche Änderung wird neben der Freigabe festgehalten. Eine längere Grenze, eine entfernte Grenze oder eine
geänderte Stufe lässt laufende Freigaben, wie sie sind.

**Bypass für.** Unter den Grenzen nennt **Bypass für**, für wen Bypass gilt, auf zwei Seiten, jede für sich:
Personen (Personen und Teams) und Agenten. Sobald eine Seite jemanden nennt, dürfen nur die auf ihr Genannten Bypass
nutzen: Eine Person muss genannt sein oder zu einem genannten Team gehören, und sind auch Agenten genannt, muss der
Agent des Chats einer von ihnen sein. Wen die Liste nicht nennt, erhält höchstens **Automatisch**. Die Liste
schränkt nur ein: Die Installation muss [Bypass](#bypass) weiterhin erlauben, und die höchste Stufe des Agenten muss
**Bypass** sein.

- **Eine Seite bleibt eingeschränkt, wenn ihre Einträge gehen**, ob hier entfernt oder mit ihrem Team, ihrer
  Person oder ihrem Agenten gelöscht. Eine Seite, auf der niemand mehr steht, nennt niemanden, Bypass gilt dann
  auf ihr für niemanden, bis Sie **Bypass für jede Person** (oder **Bypass für jeden Agenten**) wählen: Das hebt
  die Einschränkung der Seite auf und nimmt ihre Einträge von der Liste.
- **Eine engere Liste beendet laufende Bypass-Sitzungen** der Chats, die sie nicht mehr nennt, sofort, und sagt,
  wie viele: der erste Eintrag einer Seite, ein entfernter Eintrag, eine eingeschränkte Seite. Eine weitere Liste
  beendet nichts.

Jede Änderung wird im Audit-Protokoll festgehalten. Ohne gültige Enterprise-Lizenz lassen sich die Grenzen und die
Liste nicht anwenden, daher entsteht keine Freigabe, solange es welche gibt: Jeder Aufruf fragt nach. Ein Hinweis
für Administratoren sagt das, und **Aufheben** darin erlaubt wieder Freigaben innerhalb der Grenzen von Core.

**Warnungen per E-Mail.** Ist [E-Mail](mail.md) eingerichtet, erhalten Administratoren und die Adressen unter
deren **Warnungen** eine E-Mail:

- einmal am Tag, als eine Sammelmail, für jedes Einschalten seit der letzten: wer, mit welchem Agenten und welcher
  Version, auf welchem Gerät, von wann bis wann, wie und wann die Person sich erneut angemeldet hat, und den Grund,
  den sie angegeben hat, falls sie einen angegeben hat. Sie geht um 07:00 in der Zeitzone der Installation hinaus
  (die unter den Nutzungs-Einstellungen gesetzte; UTC, wenn nichts gesetzt ist). Ein Tag ohne Einschalten sendet
  keine;
- wenn sich die Meldung des Geräts einer Bypass-Sitzung von dem unterscheidet, was der Server festgehalten hat:
  sofort, einmal je Sitzung.

### Agentenaktionen {#agent-actions}

<span class="fs-enterprise" title="Erfordert Fadenstack Enterprise">Enterprise</span>

**Agenten → Governance → Agentenaktionen** zeigt, was Agenten getan haben und wie jeder Aufruf erlaubt wurde, die
neuesten zuerst:

| Zustand | Bedeutung |
|---|---|
| **Gefragt** | Der Server hat die Person gefragt, eine Antwort ist nicht festgehalten |
| **Einmal**, **Für diesen Chat**, **Immer** | Die Person hat es erlaubt, für diesen Aufruf, für den Chat oder immer (**Freigegeben** von einer Anwendung, die nicht sagt, wofür) |
| **Abgelehnt**, **Abgelaufen** | Die Person hat abgelehnt oder nicht rechtzeitig geantwortet |
| **Gesperrt** | Eine Regel hat den Aufruf abgelehnt |
| **Freigabe**, **Bypass** | Eine Freigabe auf Zeit oder eine Bypass-Sitzung deckte ihn ab, ohne Rückfrage |
| **Ohne Rückfrage** | Der Server führte ihn ohne Rückfrage aus: Der Tool-Modus des Chats erlaubte es |
| **Ausgeführt** | Das Gerät hat gemeldet, dass es ihn ausgeführt hat; eine Rückfrage davor ist eine eigene Zeile |

Jede Zeile zeigt die Zeit, die Person, den Agenten und die damals geltende Version, das Tool, das Gerät, die Runde,
das Modell und ob die Zeile die Aufzeichnung des Servers oder die Meldung des Geräts ist. Suchen Sie nach Person,
Agent, Gerät, Arbeitsbereich und Zeit (höchstens 31 Tage; standardmäßig der letzte Tag). Ein Arbeitsbereich ist
ein Dokument, ein Ordner oder eine Website: Die Suche findet die Aufrufe, die eine Freigabe dort abdeckte.
**Aufrufe** öffnet die Aufrufe einer Freigabe mit ihren Orten, Argumenten und Ergebnissen, soweit die Freigabe sie
festhielt. **Exportieren (CloudEvents)** lädt dieselben Zeilen als [CloudEvents](https://cloudevents.io/) im
Format JSON Lines herunter, ein Ereignis je Zeile, zum Einlesen in ein SIEM; von selbst wird nichts irgendwohin
gesendet.

Nur Personen mit der Rolle `approvals_auditor` können die Aktionen und den Export öffnen. Jede Suche, jeder Export
und jeder abgelehnte Versuch steht im Audit-Protokoll.

!!! note "Betriebsrat (Deutschland)"
    Die Aktionen sind Aufzeichnungen je Person, ihre Nutzung braucht daher eine Vereinbarung mit dem Betriebsrat
    (BetrVG §87 Abs. 1 Nr. 6). Sie dienen der Analyse von Vorfällen, nicht der Leistungsbeurteilung.

### Entscheidungen {#decisions}

![Agenten → Governance auf dem Reiter „Entscheidungen“: Filter für Phase, Ergebnis und Agent sowie drei Tool-Entscheidungen, Freigabe nötig, Abgelaufen und Freigegeben, jeweils mit dem, was der Nutzer erfuhr](../images/administer/governance-decisions-top.png)

**Agenten → Governance → Entscheidungen** listet auf, was die Prüfungen entschieden haben: wann, in welcher Phase
(**Eingabe**, **Antwort**, **Tool**), das Ergebnis (**Gesperrt**, **Freigabe nötig**, **Freigegeben**,
**Abgelehnt**, **Abgelaufen**), was dem Benutzer mitgeteilt wurde, den Benutzer, den Agenten und die Anfrage. Der
abgelehnte Text selbst wird nie gespeichert, nur ein mit einem Schlüssel gebildeter Fingerabdruck: Derselbe Text,
zweimal abgelehnt, zeigt denselben Wert, und niemand kann den Text daraus zurückgewinnen.

### Laufende Freigaben auf Zeit {#running-grants}

**Agenten → Governance → Freigaben auf Zeit** listet die Freigaben auf Zeit auf, die gerade laufen. Eine solche Freigabe erteilt ein Benutzer einem Agenten auf einem seiner Geräte für eine begrenzte Zeit: entweder für ein einzelnes Tool (**Für eine Zeit**) oder zum Lesen und Schreiben an benannten Orten (**Gemeinsam arbeiten**), sodass läuft, was sonst nachfragen würde. Sie gilt nie für zerstörende oder nicht klassifizierte Tools und nie für das, was eine Regel **Erst nachfragen** oder ein als **Immer nachfragen** markiertes Tool abdeckt. Jede Zeile zeigt die Person, den Agenten, was die Freigabe abdeckt, die Restzeit und die Zahl der genutzten Aufrufe. **Widerrufen** beendet eine Freigabe ab dem nächsten Aufruf des Agenten; ein Aufruf, der bereits läuft, läuft noch zu Ende. Benutzer können ihre eigenen in den **Einstellungen → Freigaben** des Agenten beenden. Eine Freigabe endet außerdem mit ihrer Zeit und sobald der Agent eine neue Version erhält.

### Bypass {#bypass}

**Bypass** ist ein fünfter Tool-Modus über **Automatisch**, für lange, sich wiederholende Arbeit, der eine Person vertraut: In einer Unterhaltung laufen zerstörende und nicht eingestufte Tools für eine gewählte Zeit ohne Nachfrage. Es ist standardmäßig aus. Benutzer schalten es in der Anwendung des Agenten ein, siehe [Tools im Chat](../use/tools.md#bypass).

**Erlauben**

1. Starten Sie den **Root-Modus**, den das Ändern dieser Einstellungen braucht.
2. Öffnen Sie **Einstellungen → Freigaben** und schalten Sie **Allow Bypass** ein (`approvals.bypass_allowed`). Das Einschalten braucht den Root-Modus, das Ausschalten nicht. Jede Änderung steht im Audit-Protokoll mit altem und neuem Wert und der Angabe, ob der Root-Modus an war.
3. Stellen Sie in der Version des Agenten unter **Freigabestufen → Höchstens** **Bypass** ein (siehe [Tools und Zustimmungen](agents.md#tools-and-approvals)). Eine solche Version lässt sich nur veröffentlichen, solange Bypass erlaubt ist.

Die Stufe **Beginnt in** kann nie Bypass sein, daher beginnt keine Unterhaltung darin. Eine [Grenze für den Tool-Modus](#tool-mode-limits) unter Bypass (<span class="fs-enterprise" title="Erfordert Fadenstack Enterprise">Enterprise</span>) nimmt die Wahl weg.

**Einstellungen → Freigaben** enthält außerdem:

| Einstellung | Standard | Was sie bewirkt |
|---|---|---|
| **Longest grant (minutes)** (`approvals.longest_grant_minutes`) | 120 (15 bis 720) | Die längste Dauer jeder Freigabe: auf Zeit, für einen Arbeitsabschnitt oder Bypass. Die eigene längste Freigabe eines Agenten darf nur kürzer sein. |
| **Keep Bypass records (days)** (`approvals.bypass_retention_days`) | 365 (90 bis 1095) | Bypass-Sitzungen und ihre Aufrufe werden so lange nach ihrem Ende gelöscht. |
| **Result summary (characters)** (`approvals.result_summary_chars`) | 500 (0 bis 2000) | Wo eine Freigabe Ergebnisse festhält, wie viel vom Ergebnis jedes abgedeckten Aufrufs bleibt, nachdem personenbezogene Daten maskiert wurden. 0 hält nur fest, ob er gelang, und den Text eines Fehlers. Ein Fehler behält immer seine ersten 500 Zeichen. |

**Die erneute Anmeldung.** Um Bypass einzuschalten, meldet sich eine Person im Browser erneut an. Das lässt sich nicht abschalten. **Freigaben auf Zeit** (und, für Inhaber von `approvals_auditor`, die Aufrufe einer Sitzung) zeigen, wie und wann die Person sich erneut angemeldet hat: Passwort, OpenID Connect, SAML oder Verzeichnis, mit dem Namen des Anbieters. Prüfen Sie das bei Ihrem Anmelde-Anbieter, bevor Sie Bypass erlauben:

- Ein OpenID-Connect-Anbieter wird mit `prompt=login` und `max_age=0` gefragt und muss mit `auth_time` antworten.
- Ein SAML-Anbieter wird mit `ForceAuthn` gefragt, und das `AuthnInstant` seiner Assertion muss später als die Anfrage liegen.
- Ein Anbieter, der aus einer älteren Sitzung antwortet, wird abgelehnt: „Der Anmelde-Anbieter hat keine frische Anmeldung bestätigt“.
- Ein Verzeichnis (LDAP) fragt erneut nach dem Passwort.

Anwendungen, die die erneute Anmeldung nicht können (ältere SDK-Versionen), erhalten Bypass nicht angeboten.

**Was Bypass nicht überspringt.** Ausgeblendete und abgelehnte Tools bleiben es. Regeln **Deny**, Regeln **Erst nachfragen** und Tools, die der Autor des Agenten mit **Immer nachfragen** markiert hat, gelten weiter. Tools, die an Maschinen oder in der physischen Welt handeln, werden noch nicht von selbst erkannt: Markieren Sie sie mit **Immer nachfragen** oder legen Sie eine Regel **Erst nachfragen** an.

**Was festgehalten wird.** Jeder Aufruf unter Bypass wird auf dem Server festgehalten: das Tool, die Zeit und seine Argumente nach dem Maskieren personenbezogener Daten, lesend wie schreibend. Am Ende jeder Runde meldet das Gerät der Person die Aufrufe, die es ausgeführt hat, und der Server vergleicht; jeder Unterschied wird gekennzeichnet. Bypass selbst wird festgehalten, bevor es wirkt, und ohne Bestätigung des Servers gibt es kein Bypass.

**Beobachten und beenden.** **Agenten → Governance → Freigaben auf Zeit** listet laufende Bypass-Sitzungen mit etwaigen Unterschieden, und **Widerrufen** beendet eine (siehe [Laufende Freigaben auf Zeit](#running-grants)).

**Ausschalten.** Schalten Sie **Allow Bypass** aus, wirkt Bypass sofort wie **Automatisch** (innerhalb einer halben Minute auf jedem Server-Worker), und laufende Bypass-Sitzungen enden, festgehalten als durch den Schalter beendet.

**Wer lesen darf, was lief.** Nur Personen mit der Rolle `approvals_auditor`. Bei der Installation hat sie niemand, und Administrator zu sein genügt nicht. Jedes Lesen und jeder abgelehnte Versuch steht im Audit-Protokoll. Die Liste der Konsole zeigt das Tool, die Zeit, welche Seite es ausführte (Gerät oder Server), ob das Gerät es gemeldet hat, Unterschiede und, soweit die Freigabe sie festhielt, den Ort, die Argumente und das Ergebnis. Sie bleiben auf dem Server verschlüsselt und werden nur für das Lesen entschlüsselt. Mit Enterprise durchsucht [Agentenaktionen](#agent-actions) sie über Sitzungen hinweg.

!!! note "Betriebsrat (Deutschland)"
    Aufzeichnungen je Person brauchen eine Vereinbarung mit dem Betriebsrat (BetrVG §87 Abs. 1 Nr. 6). Die Aufzeichnungen dienen der Analyse von Vorfällen, nicht der Leistungsbeurteilung.

## Was Benutzer sehen, wenn etwas gestoppt wird {#what-people-see-when-something-is-stopped}

| Was geschehen ist | Im Chat |
|---|---|
| Eine Tool-Regel hat ein Tool gesperrt | Die Tool-Zeile zeigt **Nicht ausgeführt**. Das Modell erfährt es, mit der Nachricht der Regel, und macht ohne das Tool weiter. |
| Ein Tool braucht eine Freigabe | „… möchte ausgeführt werden“, mit **Zulassen** und **Ablehnen**. Kommt innerhalb von fünf Minuten keine Antwort, wird der Aufruf nicht ausgeführt (**Abgelaufen**). |
| Eine gespeicherte Richtlinie gilt, aber das PII-Modul läuft nicht, und die Richtlinie blockiert | Die Antwort schlägt fehl mit „A PII policy is set for this workspace, but the PII module is not running, so nothing was sent.“ |
| Der Filter für personenbezogene Daten konnte nicht laufen, und die Richtlinie blockiert | Die Antwort schlägt fehl mit „PII service unavailable and fail_action=block.“ An das Modell wurde nichts gesendet. |
| Die Richtlinie passt nicht zur installierten Erkennung (ein Schwellenwert für das ältere Profil oder eine Kategorie, die sie nicht findet), und die Richtlinie blockiert | Die Antwort schlägt fehl mit „The PII policy does not fit the installed PII detector (…), so nothing was sent.“ Übernehmen Sie das installierte Profil in der Richtlinie (siehe [oben](#adopt-an-installed-detector-profile)). |
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
| Tools | Tool-Regeln, Tool-Klassen, Tool-Modi | Grenzen für den Tool-Modus für alle, ein Team, einen Agenten oder eine Person; Inhaltsrichtlinien für Tool-Argumente <span class="fs-planned">Geplant</span> |
| Freigaben | Freigaben auf Zeit, gemeinsames Arbeiten, Bypass; die längste Freigabe der Installation | Freigabe-Grenzen für alle, ein Team, einen Agenten oder eine Person; für wen Bypass gilt; Warnungen per E-Mail |
| Die Aufzeichnung | Entscheidungen, ohne den Text; die Aufrufe einer Freigabe für Prüfer | Agentenaktionen für Prüfer, mit einem Export als CloudEvents; die Namen der Richtlinien <span class="fs-planned">Geplant</span> |

Inhaltsrichtlinien werden eine Eingabe, eine Antwort oder einen Tool-Aufruf anhand ihres Inhalts ablehnen.
