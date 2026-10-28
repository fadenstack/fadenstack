# Agenten und Add-ins {#agents-and-add-ins}

Ein Agent ist der Assistent in einer anderen Anwendung: die Seitenleiste des Fadenstack-Browser-Agenten für
Chrome oder der Aufgabenbereich des Add-ins für Word, Excel, PowerPoint oder Outlook. Die Anwendung bringt die
Tools mit (die Seite lesen, ein Formular ausfüllen, eine Zelle schreiben); Fadenstack entscheidet, wer den
Agenten nutzen darf, welches Modell antwortet und welche Tools der Anwendung er ausführen darf. Sie richten pro
Anwendung einen Agenten ein und gewähren ihn den Teams, die ihn haben sollen.

Was Benutzer mit den Agenten tun, ist in [Der Browser-Agent](../use/browser-agent.md) und
[Office-Add-ins](../use/office-add-ins.md) beschrieben.

![Agenten → Agenten: jeder Agent mit Adresse, Beschreibung, Status (Veröffentlicht oder Stillgelegt), aktiver Version und wann er geändert wurde, oben „Neuer Agent“](../images/administer/agents.png)

## Bevor Sie beginnen {#before-you-start}

- **Die Anwendung** ist für Ihre Benutzer installiert (siehe [Die Anwendungen installieren](#install-the-applications)).
- **Ein Modell**, mit dem der Agent antwortet: eine Ihrer eigenen Bereitstellungen oder das Modell eines
  externen Providers. Alles, was eine Person fragt, und die Seite oder das Dokument, das der Agent dafür liest,
  geht an dieses Modell; wählen Sie es also mit derselben Sorgfalt wie für den Chat (siehe
  [Was die Organisation verlässt](providers.md#what-leaves-the-organisation)).
- **Eine Gruppe** mit den betreffenden Personen (**Zugriffskontrolle → Gruppen**) oder die Personen selbst: Ein
  Agent wird Personen und Gruppen gewährt und allen anderen verweigert.

## Den Agenten erstellen {#create-the-agent}

![Neuer Agent mit Chrome unter „Von einer App ausgehen“: der Name, die Adresse browser-assistant, das noch zu wählende Modell, „Wer ihn nutzen darf“ und „Sofort veröffentlichen“ angehakt](../images/administer/new-agent.png)

1. Öffnen Sie **Agenten → Agenten** und wählen Sie **Neuer Agent**.
2. Wählen Sie unter **Von einer App ausgehen** die Anwendung: **Chrome**, **Microsoft Word**,
   **Microsoft Excel**, **Microsoft PowerPoint** oder **Microsoft Outlook**. Ihre Definition wird ausgefüllt:
   die Anweisungen, die Tools, die die Anwendung mitbringt, mit ihren Klassen, und die Begrüßung.
3. Behalten Sie die **Adresse** bei. Nach ihr fragt die Anwendung, und sie lässt sich später nicht ändern:

    | Anwendung | Adresse |
    |---|---|
    | Chrome | `browser-assistant` |
    | Word | `word-assistant` |
    | Excel | `excel-assistant` |
    | PowerPoint | `powerpoint-assistant` |
    | Outlook | `outlook-assistant` |

4. Wählen Sie das **Modell**. Es ist Pflicht: Die Anwendungen überlassen die Wahl des Modells dem Agenten, und
   ohne Modell kann der Agent nicht antworten.
5. Geben Sie unter **Wer ihn nutzen darf** einen Teil eines Gruppennamens oder eine E-Mail-Adresse ein und
   wählen Sie den Eintrag aus.
6. Setzen Sie das Häkchen bei **Sofort veröffentlichen**, um den Agenten diesen Personen sofort bereitzustellen.
   Ohne Häkchen beginnt er als Entwurf, den nur seine Betreuer nutzen können.
7. Wählen Sie **Erstellen**.

Die Seite des Agenten öffnet sich. Sein Status in der Liste ist **Entwurf**, **Veröffentlicht** oder
**Stillgelegt**.

## Die Seite des Agenten {#the-agents-page}

![Die Seite des Word-Assistenten auf dem Reiter „Definition“: die Reiter Definition, Versionen, Apps, Zugriff und Aktivität, oben Bearbeiten und Stilllegen, die Anweisungen und das Modell](../images/administer/agent-definition.png)

| Reiter | Was er enthält |
|---|---|
| **Definition** | Die Version, die Benutzer erhalten: **Anweisungen**, **Modell**, **Wissen** (Sammlungen, die er durchsuchen darf), **Server-Tools** (**Wie im Chat**, **Nur diese** oder **Keine**), **Lokale MCP-Server**, **Host-Tools** (die Tools der Anwendung und ihre Klassen), **Darstellung** (Begrüßung und vorgeschlagene Prompts) und **Datenschutz**. |
| **Versionen** | Jede Version. Mit **Veröffentlichen** eine Version veröffentlichen, mit **Zurücksetzen** zu einer früheren zurückkehren, unter **Änderungen** die Unterschiede sehen. |
| **Zugriff** | **Wer ihn nutzen darf**, die Liste **Ausgeschlossene Personen** (Enterprise) und seine **Betreuer** (mit Enterprise: **Betreuer und Prüfer**). **Zugriff prüfen** zeigt, ob eine Person den Agenten nutzen darf, und warum. |
| **Aktivität** | Nutzung über einen Zeitraum (Anfragen, Fehler, Tokens, Benutzer, Unterhaltungen: nur Zahlen, nie Inhalte), nur, wenn mindestens fünf Personen den Agenten im Zeitraum genutzt haben; der Eigentümer eines persönlichen Agenten sieht dessen Zahlen trotzdem. Was die Anwendungen gemeldet haben (ausgeführte Tools, Zustimmungen) und **Angemeldete Geräte**. Der Reiter nennt niemanden außer Ihnen selbst. |
| **Apps** | Was die Anwendungen mitbringen, verglichen mit der Version des Agenten (siehe [Wenn eine Anwendung neue Tools mitbringt](#when-an-application-brings-new-tools)). |

??? info "Mehr zu Server-Tools"
    **Nur diese** nimmt Namen oder Muster (`mcp.github.*`, `knowledge_search`, `current_time`,
    `get_available_tools`); **Keine** führt nichts auf. Die Begrenzung gilt für jedes Tool, das das Gateway für den
    Agenten ausführt: MCP-Tools, die Wissenssuche, an die Unterhaltung angehängte Dateien und die System-Tools. Ein
    Tool außerhalb davon wird nicht angeboten, von der Tool-Suche nicht gefunden und mit einem Fehler abgelehnt,
    wenn das Modell (oder Text, den die Anwendung einspeist) es trotzdem nennt. Die eigenen Tools der Anwendung
    (**Host-Tools**) und die Dateien eines Skills, den der Agent nutzt, bleiben unberührt.

??? info "Mehr zu lokalen MCP-Servern"
    **Lokale MCP-Server**: **Nicht erlaubt** (die Voreinstellung) beschränkt den Agenten auf die Tools des Servers
    und die Tools der Anwendung, die seine Version unter **Host-Tools** nennt; eine Version, die keine nennt, hält
    nichts zurück, doch dann ist jedes Tool der Anwendung nicht eingestuft und fragt jedes Mal zuerst. **Erlaubt**
    lässt ihn MCP-Server auf dem Computer der Person einbinden; deren Tools sind nicht
    eingestuft und fragen deshalb immer zuerst. Personen im **Entwicklermodus** (siehe
    [Benutzer, Gruppen und Rollen](users.md#developer-mode)) dürfen sie in jedem Fall einbinden.

![Der Reiter „Versionen“ des Word-Assistenten: v2 aktiv mit Ansehen und Änderungen, v1 mit Ansehen und Zurücksetzen, unterhalb der Reiter des Agenten und der Schaltflächen Bearbeiten und Stilllegen](../images/administer/agent-versions.png)

Das Speichern der Definition erzeugt eine neue Version (**Als neue Version speichern** oder **Speichern und
veröffentlichen**); Versionen ändern sich nie. Eine veröffentlichte Version erreicht alle, die den Agenten
nutzen, bei ihrer nächsten Anfrage.

![Der Reiter „Zugriff“: „Wer ihn nutzen darf“ mit „Person oder Team hinzufügen“ und den Personen, denen der Agent gewährt ist](../images/administer/agent-access.png)

**Betreuer** ändern den Agenten, veröffentlichen Versionen, entscheiden, wer ihn nutzen darf, und können ihn
schon vor der Veröffentlichung nutzen. Das tun sie über **Agenten** im Benutzermenü des Chats; Zugriff auf die
Konsole brauchen sie nicht.

Mit Enterprise kann ein Agent auch **Prüfer** haben: Wählen Sie **Hinzufügen als** → **Prüfer**, bevor Sie
die Person auswählen, oder ändern Sie die Rolle eines Mitglieds in der Liste, und wählen Sie **Speichern**.
Prüfer sehen die Versionen, Mitglieder, Einstellungen und Aktivität des Agenten und ändern nichts; nutzen
können sie ihn nur, wenn er ihnen wie allen anderen freigegeben ist. Ohne gültige Enterprise-Lizenz sehen sie
ihn nicht mehr.

Um den Zugriff einer Person zu prüfen, geben Prüfer ihre ganze E-Mail-Adresse ein: Ein Teil davon findet
niemanden, so listet die Prüfung nicht alle auf (DSGVO Art. 5 Abs. 1 lit. c, Art. 25 Abs. 2). Jede Prüfung wird
im Audit-Protokoll festgehalten, mit wer wen geprüft hat (`agent.access.check`).

!!! note "Betriebsrat"
    Prüfer lesen, was Personen mit einem Agenten getan haben. Eine technische Einrichtung, die Verhalten oder
    Leistung der Beschäftigten überwachen kann, braucht die Zustimmung des Betriebsrats (BetrVG § 87 Abs. 1
    Nr. 6): Vereinbaren Sie die Prüferrolle mit ihm, bevor Sie sie vergeben.

Mit Enterprise dürfen **Ausgeschlossene Personen** den Agenten nicht nutzen, auch nicht über ein Team, das ihn
nutzen darf: Wählen Sie die Person, geben Sie eine **Begründung** an und wählen Sie **Ausschließen**. Das wirkt
in den Anwendungen sofort und wird im Audit-Protokoll festgehalten. Die Person erfährt nur,
dass sie ausgeschlossen ist; die Begründung ist für die Betreuer und Administratoren des Agenten. Betreuer und
Administratoren behalten den Agenten immer und können nicht ausgeschlossen werden.

Ohne gültige Enterprise-Lizenz können einen Agenten mit ausgeschlossenen Personen nur seine Betreuer nutzen,
damit niemand Ausgeschlossenes ihn zurückbekommt; der Reiter **Zugriff** sagt das.
**Für alle Berechtigten öffnen** hebt das auf: Alle, denen der Agent freigegeben ist, können ihn wieder nutzen.
Sobald die Lizenz wieder gültig ist, bleiben die noch aufgeführten ausgeschlossenen Personen wieder außen vor.

![Der Reiter „Aktivität“: Anfragen, Fehler, Tokens, Nutzer und Gespräche der letzten 30 Tage mit einem Diagramm, darunter, was die Anwendungen gemeldet haben, etwa ausgeführte Tools und Freigaben](../images/administer/agent-activity.png)

## Die Anwendungen installieren {#install-the-applications}

**Chrome.** Installieren Sie die Erweiterung für Ihre Benutzer mit der Chrome-Richtlinie
`ExtensionInstallForcelist`, und geben Sie Server und Agent im verwalteten Speicher der Erweiterung vor
(Chrome-Richtlinie `3rdparty`), sodass Benutzer sich nur anmelden und beides nicht ändern können:

```json
{ "serverUrl": "https://ai.example.internal", "agent": "browser-assistant" }
```

Mit `"language": "de"` legen Sie die Sprache der Seitenleiste für alle fest; ohne wählt jede Person sie in den
Einstellungen der Seitenleiste, und **Automatisch** ist die Sprache des Browsers.

Mit `"webSearch": true` schalten Sie die Websuche für alle ein, mit `false` lassen Sie sie aus; ohne die Angabe
entscheidet jede Person in den Einstellungen der Seitenleiste, und bis dahin ist sie aus. Suchanfragen gehen an die
in Chrome eingestellte Suchmaschine, nicht über Fadenstack; die Chrome-Richtlinie `DefaultSearchProviderSearchURL`
kann sie auf Ihren eigenen Suchdienst richten.

Um den Agenten von bestimmten Websites fernzuhalten (Onlinebanking, Personalsysteme), sperren Sie sie für die
Erweiterung mit der Chrome-Richtlinie `ExtensionSettings` (`runtime_blocked_hosts` unter der ID der Erweiterung):
Chrome lässt sie diese Websites dann weder lesen noch darauf handeln, was immer eine Person erlaubt.

Ein Agent aus der Chrome-Vorlage bekommt die Wissenssuche und die Uhrzeit als **Server-Tools**; weitere, etwa eine
Websuche, fügen Sie in seiner **Definition** hinzu. Screenshots und Bilder gehen nur an ihn, wenn sein **Modell**
Bilder sieht.

Chrome nutzt den Zertifikatspeicher des Betriebssystems: Die Computer müssen dem Zertifikat des Servers
vertrauen (siehe [HTTPS und Vertrauen](../operate/https.md)).

**Office.** Die Add-ins laufen im Desktop-Office für Windows: Word, Excel, PowerPoint und Outlook, getestet nur mit
den Apps von Microsoft 365. Bei Outlook nur im klassischen Outlook: Das neue Outlook für Windows führt solche
Add-ins nicht aus. Die IT gibt Server und Agenten in der Computerrichtlinie
`%PROGRAMDATA%\Fadenstack\Office\policy.json` vor; was dort festgelegt ist, können Benutzer nicht ändern:

```json
{
  "serverUrl": "https://ai.example.internal",
  "agents": {
    "word": "word-assistant",
    "excel": "excel-assistant",
    "powerpoint": "powerpoint-assistant",
    "outlook": "outlook-assistant"
  },
  "trustedCaCertificate": null
}
```

Mit `"language": "de"` legen Sie die Sprache der Bereiche für alle fest; ohne wählt jede Person sie in den
Einstellungen des Bereichs, und **Automatisch** ist die Anzeigesprache von Office. Die Add-ins bringen Englisch,
Deutsch, Französisch, Spanisch und vereinfachtes Chinesisch mit: Verteilen Sie die Ordner `de`, `fr`, `es` und
`zh-Hans` neben dem Add-in, sonst bleiben die Bereiche auf Englisch.

Eine Anmeldung gilt für alle Office-Add-ins auf einem Computer.

??? info "Mehr zu trustedCaCertificate"
    `trustedCaCertificate` nennt eine CA-Zertifikatsdatei, der für den Server vertraut werden soll – für eine
    Installation, deren CA nicht an die Computer verteilt ist.

Benutzer melden sich mit ihrem Fadenstack-Konto an. Ihre Unterhaltungen bleiben auf ihrem Computer; der Server
speichert Nutzung und Ereignisse (welche Tools liefen, wie lange, mit welchem Ergebnis), nie den Inhalt. Was
der Agent liest, erreicht das Modell über Fadenstack, daher gilt Ihre
[Richtlinie für personenbezogene Daten](policies.md).

## Tools und Zustimmungen {#tools-and-approvals}

Jedes Tool, das eine Anwendung mitbringt, hat eine Klasse, die die Version des Agenten festlegt:

| Klasse | In den Tool-Modi |
|---|---|
| **Lesen** | Läuft in jedem Tool-Modus außer **Aus**. |
| **Schreiben** | Fragt im Modus **Fragen** zuerst nach und läuft im Modus **Automatisch**. |
| **Zerstörend** | Fragt immer nach. |
| **Nicht eingestuft** | Fragt immer nach. |

Benutzer wählen den Tool-Modus im Bereich des Agenten, pro Unterhaltung; jede Unterhaltung beginnt mit
**Fragen**. Die Anwendung kann eine Klasse verschärfen, nie lockern. Ihre [Tool-Regeln](policies.md#tool-rules)
gelten für Agenten ebenso wie für Chats. Zwei Beispiele: Das Outlook-Add-in sendet nie selbst E-Mails, es öffnet
Entwürfe für die Person; und der Browser-Agent liest oder füllt nie Passwortfelder oder Kartenfelder, die die
Website als solche kennzeichnet.

## Wenn eine Anwendung neue Tools mitbringt {#when-an-application-brings-new-tools}

Bei jedem Start teilt eine Anwendung dem Server mit, was sie mitbringt: ihre Tools mit der Klasse, die ihr
Entwickler ihnen gegeben hat, den Kontext, den sie anbietet, ihre Begrüßung und ihre Version. Der Reiter
**Apps** vergleicht das mit der Version des Agenten.

Bringt ein Update der Anwendung ein Tool mit, das die Version des Agenten nicht aufführt, geht nichts kaputt:
Das Tool wird zurückgehalten (das Modell sieht es nicht), und die Anwendung arbeitet mit den Tools weiter, die
sie nutzen darf. Das [Dashboard](dashboard.md) zeigt „Wartende Tool-Änderungen bei …“, bis Sie entscheiden.

![Der Reiter „Apps“ eines Agenten ohne offene Entscheidung: Version 2 enthält alles, was die Apps mitbringen, und jedes Tool mit dem Status „Im Vertrag“, seiner Klasse und dem Add-in, das es mitbringt](../images/administer/agent-apps.png)

1. Öffnen Sie den Reiter **Apps** des Agenten. Jedes Tool zeigt einen Status:

    | Status | Bedeutung |
    |---|---|
    | **Neu** | Die Version des Agenten führt es nicht auf: Es wird zurückgehalten. |
    | **Geändert** | Seine Parameter weichen von den übernommenen ab. |
    | **Riskanter** | Die Anwendung stuft es jetzt riskanter ein als der Agent. |
    | **Ungenutzt** | Steht in der Version des Agenten, aber keine genutzte Anwendung bringt es mit. |
    | **Im Vertrag** | Nichts zu tun. |

2. Lesen Sie die Beschreibung des Tools, besonders wenn sie eine Warnung trägt: **Weist das Modell an**,
   **Versteckte Zeichen**, **Link** oder **Lang**. Modelle lesen Tool-Beschreibungen wie Anweisungen.
3. Behalten oder ändern Sie die Klasse, die die Anwendung vorschlägt, oder wählen Sie **Weglassen**.
4. Wählen Sie **Übernehmen und veröffentlichen** oder **Als Entwurf übernehmen**, um später unter **Versionen**
   zu veröffentlichen.

Ohne diesen Klick wird nichts übernommen. **Apps im Einsatz** listet jede Anwendung und Version auf, die den
Agenten ausführt, wer sie zuletzt gemeldet hat und welchen Kontext sie anbietet.

## Aus der Erweiterung erstellte Agenten {#agents-created-from-the-extension}

Wer eine Rolle mit `agents:create` hat, kann auch in der Chrome-Erweiterung beginnen: Findet sie unter ihrer
Adresse keinen Agenten, bietet sie **Create it from this extension** an. Der Agent wird aus der Definition der
Erweiterung als Entwurf erstellt, den diese Person betreut. Veröffentlicht und anderen gewährt wird er dann in
der Konsole oder auf ihrer Seite **Agenten**.

## Stilllegen und löschen {#retire-and-delete}

| Um | So geht's |
|---|---|
| den Agenten sofort für alle zu sperren | **Stilllegen**: Anmeldungen funktionieren nicht mehr; seine Versionen, Zugriffe und sein Verlauf bleiben erhalten. **Wiederherstellen** holt ihn so zurück, wie er war. |
| einen stillgelegten Agenten endgültig zu löschen | **Löschen**, dann zur Bestätigung seine Adresse eingeben. Seine Versionen, Zugriffe und Anmeldungen werden entfernt; was er getan hat, bleibt in den Nutzungs- und Audit-Logs, und seine Adresse ist für einen neuen Agenten frei. Zum Löschen ist die Berechtigung `agents:delete` nötig, die die integrierte Rolle `admin` hat, Betreuer aber nicht. |
| die Anmeldung eines Geräts zu beenden | **Abmelden** auf dem Reiter **Aktivität**: Die Person muss sich erneut anmelden. |

??? info "Wie lange Agent-Anmeldungen gelten"
    Eine Agent-Anmeldung auf einem Gerät gilt 30 Tage und endet nach 7 Tagen ohne Nutzung. Das Entziehen des
    Zugriffs wirkt innerhalb von zehn Minuten. Diese Werte sind **Agent sign-in lifetime (s)**,
    **Agent sign-in idle timeout (s)** und **Agent access token lifetime (s)** unter **Einstellungen → Allgemein**.

    Mit Enterprise kann ein Agent kürzere haben: **Gültigkeit der Anmeldung** auf seinem Reiter **Definition**,
    in Sekunden, mindestens 60; ein leeres Feld behält den Wert des Mandanten. Sie gelten mit der Version, die
    Sie speichern und veröffentlichen. Längere als die des Mandanten werden abgelehnt, und wird der Wert des
    Mandanten später gesenkt, verkürzt das auch die des Agenten. Bereits angemeldete Geräte erhalten die kürzere
    Gültigkeit, wenn ihre Anmeldung das nächste Mal erneuert wird.

## Wenn es nicht funktioniert {#when-it-does-not-work}

Der Agent zeigt in der Anwendung an, warum eine Anmeldung fehlgeschlagen ist:

| Sie sehen | Was Sie tun |
|---|---|
| **Unter dieser Adresse gibt es keinen Agenten.** | Die Adresse in der Anwendung weicht von der des Agenten ab. Vergleichen Sie sie mit der Liste **Agenten**; ein Agent, der ohne **Von einer App ausgehen** erstellt wurde, hat eine aus seinem Namen abgeleitete Adresse. |
| **Die Person hat keinen Zugriff.** | Fügen Sie sie oder ihre Gruppe auf dem Reiter **Zugriff** hinzu, und prüfen Sie, ob der Agent veröffentlicht ist. **Zugriff prüfen** zeigt, was sie daran hindert, etwa ein Ausschluss. |
| **Der Server antwortet nicht.** | Prüfen Sie die Serveradresse in der Anwendung und ob der Computer dem Zertifikat des Servers vertraut. |
| Ein Tool, das die Anwendung anbietet, wird nicht genutzt | Es wird zurückgehalten: Übernehmen Sie es auf dem Reiter **Apps**. |
