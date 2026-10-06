# MCP-Server {#mcp-servers}

Ein MCP-Server gibt Chats und Agenten Tools: ein Repository durchsuchen, ein Ticket anlegen, eine Datei lesen.
Sie registrieren den Server einmal; Fadenstack verbindet sich mit ihm, listet seine Tools auf und führt ein Tool
aus, wenn ein Modell es anfordert – nach den Regeln, die Sie festlegen.

## Bevor Sie beginnen {#before-you-start}

- Das Modul **MCP** ist eingeschaltet (**Einstellungen → Module**; siehe [Module](index.md#modules)). Seine
  Seiten finden Sie unter **Erweiterungen → MCP Hub**.
- Die Adresse des Servers oder der Befehl, der ihn startet, und die Zugangsdaten, die er braucht. Ein Server,
  der MCP über HTTP spricht, kann auch als Container auf dem Fadenstack-Server selbst laufen (siehe
  [Container](../operate/containers.md)).
- Die Modelle, mit denen Ihre Benutzer Tools nutzen, können Tools aufrufen: Bei Ihren eigenen Modellen ist
  **Tool-Aufrufe** in den [Engine-Einstellungen](deployments.md#engine-settings) der Bereitstellung
  eingeschaltet, bei einem externen Provider **Tools des Gateways anbieten** (siehe
  [Externe Provider](providers.md)).

## Einen Server registrieren {#register-a-server}

1. Öffnen Sie **Erweiterungen → MCP Hub** und wählen Sie **Server registrieren**.
2. Füllen Sie das Formular aus:

    | Feld | Was Sie eingeben |
    |---|---|
    | **Servername** | Der Name, den die Konsole anzeigt. |
    | **Transport** | **Streamable HTTP** für aktuelle Server, **SSE (älteres HTTP)** für ältere oder **Stdio (Befehl)** für einen Server, den der MCP-Dienst selbst startet. |
    | **Server-URL** | Bei HTTP: die MCP-Adresse des Servers, meist mit `/mcp` (oder `/sse`) am Ende, z. B. `https://tools.example.internal/mcp`. |
    | **Befehl** | Bei Stdio: der Befehl und seine Argumente, z. B. `uvx mcp-server-time`. |
    | **Tool-Präfix** | Ein kurzer, eindeutiger Name. Die Tools des Servers heißen `mcp.<prefix>.<tool>`, z. B. `mcp.github.search_repositories`. Bleibt das Feld leer, wird einer erzeugt. |
    | **PII-Modus** | Was der Server von personenbezogenen Daten sehen darf (siehe [Personenbezogene Daten](#personal-data-pii-mode)). |
    | **Zeitlimit (Sekunden)** | Wie lange der Verbindungsaufbau und jeder Tool-Aufruf dauern dürfen: 5 bis 600 Sekunden. |

    ![Der Dialog Server registrieren: Servername, Transport auf Streamable HTTP, Server-URL, Tool-Präfix, PII-Modus, Zeitlimit (Sekunden) und Header mit Header hinzufügen](../images/administer/mcp-register-server.png)

3. Fügen Sie unter **Header** (HTTP) oder **Umgebungsvariablen** (Stdio) hinzu, was der Server für den Zugang
   braucht, z. B. `Authorization: Bearer <token>`.
4. Wählen Sie **Registrieren**.

Fadenstack verbindet sich mit dem Server und listet seine Tools auf. Der Server erscheint dann als aktiv, mit der
Zahl seiner Tools.

![MCP-Server mit zwei Servern, beide aktiv, mit ihrem Transport, der Zahl ihrer Tools und ihrem PII-Modus, oben Netzwerk durchsuchen und Server registrieren](../images/administer/mcp-servers.png)

!!! warning "Alle nutzen diese Zugangsdaten"
    Header und Variablen werden verschlüsselt gespeichert und nie wieder angezeigt; die Konsole listet nur ihre
    Namen. Jede Person, deren Chat die Tools des Servers nutzt, handelt mit diesen Zugangsdaten. Geben Sie dem
    Server ein Token mit nur den Rechten, die die Tools brauchen, wo möglich nur mit Leserechten.

**Netzwerk durchsuchen** sucht auf einem Host in einem Portbereich nach MCP-Servern und bietet die gefundenen
zur Registrierung an.

## Die Seite eines Servers {#a-servers-page}

Wählen Sie einen Server in der Liste aus, um seine Seite zu öffnen. Sie zeigt Status, Transport, Präfix,
PII-Modus, Zeitlimit und welche Header gesetzt sind und, wenn die letzte Verbindung fehlgeschlagen ist,
**Der letzte Verbindungsversuch ist fehlgeschlagen** mit dem Grund.

- **Entdeckte Tools** listet jedes Tool auf, das der Server anbietet. Der Schalter **Aktiviert** neben einem
  Tool schaltet es für alle aus (oder ein).
- **Aktiviert** oben schaltet den ganzen Server aus oder ein.
- **Aktualisieren** listet die Tools neu auf; **Testen** prüft die Verbindung und zählt die Tools.
- **Bearbeiten** ändert URL oder Befehl, Zeitlimit, PII-Modus sowie Header oder Variablen. Ein gespeicherter
  Header wird einzeln ersetzt oder entfernt, ohne dass Sie die anderen neu eingeben müssen. Beim Speichern wird
  der Server neu verbunden, und seine Tools werden neu aufgelistet.
- **Server löschen** entfernt ihn mit allen seinen Tools.

![Einen Server bearbeiten: Server-URL, Zeitlimit (Sekunden) und PII-Modus sowie der gespeicherte Authorization-Header, als gesetzt markiert, mit Ersetzen und Entfernen](../images/administer/mcp-edit-server.png)

## Wer die Tools nutzen kann {#who-can-use-the-tools}

Die Tools eines registrierten, aktivierten Servers stehen jedem Chat in der Organisation und den Agenten zur
Verfügung. Eine Liste von MCP-Servern pro Person gibt es nicht. Was ausgeführt wird, steuern Sie so:

- **Tools ausschalten** unter **Entdeckte Tools**, für Tools, die niemand nutzen soll.
- **Tool-Regeln** unter **Agenten → Governance**: ein Tool verbieten, es vor der Ausführung von der Person
  bestätigen lassen oder es erlauben, nach Name oder Muster, z. B. `mcp.github.*` (siehe
  [Richtlinien und personenbezogene Daten](policies.md)).
- **Tool-Klassen** unter **Agenten → Governance**: festlegen, ob ein Tool nur liest, schreibt oder löscht. Der
  Tool-Modus, den Benutzer im Chat wählen, richtet sich nach dieser Klasse. Ein Tool, das niemand eingestuft
  hat, fragt immer, bevor es läuft.
- **Die Server-Tools eines Agenten**: Jeder Agent erhält, was ein Chat erhält, nur die Tools, die Sie aufführen
  (z. B. `mcp.github.*`), oder keine (siehe [Agenten und Add-ins](agents.md)).

## Wie Benutzer die Tools im Chat erhalten {#how-people-get-the-tools-in-a-chat}

- **Jedes MCP-Tool ist in einem neuen Chat zunächst ausgeschaltet.** Jedes Tool, das dem Modell angeboten wird,
  braucht in jeder Nachricht Platz; deshalb werden nur die Tools gesendet, die ein Chat braucht.
- Ein Tool wird eingeschaltet, wenn die Person es im Bereich **Tools** des Chats (der Schraubenschlüssel)
  einschaltet, wenn ihre Nachricht es nennt oder wenn das Modell es mit seiner Tool-Suche findet. Einmal
  eingeschaltet, bleibt es für diesen Chat eingeschaltet.
- **Der Tool-Modus** entscheidet, was ohne Nachfrage laufen darf: **Aus**, **Nur lesen**, **Fragen** (damit
  beginnt jeder Chat: Tools, die lesen, laufen, jedes andere Tool wartet auf die Zustimmung der Person) oder
  **Automatisch** (Tools, die lesen oder schreiben, laufen; zerstörende und nicht eingestufte fragen nach).

[Tools im Chat](../use/tools.md) erklärt das für die Benutzer, die damit arbeiten.

Gehostete Server mit vielen Tools lassen sich oft über einen Header eingrenzen. Der Server von GitHub z. B.
akzeptiert `X-MCP-Toolsets: repos` oder `X-MCP-Readonly: true`.

## Personenbezogene Daten (PII-Modus) {#personal-data-pii-mode}

Ist das Modul **Datenschutz (PII)** eingeschaltet, kann die PII-Richtlinie des Chats personenbezogene Daten
durch Platzhalter ersetzen, bevor ein Modell sie sieht. Was ein Tool dann erhält, hängt vom **PII-Modus** seines
Servers ab:

| Modus | Das Tool erhält |
|---|---|
| **Zulassen (keine Filterung)** | Die echten Werte. |
| **Schwärzen (personenbezogene Daten entfernen)** | Die Platzhalter; personenbezogene Daten, die in den Argumenten des Aufrufs gefunden werden, werden ebenfalls entfernt. |
| **Blockieren (ablehnen, wenn personenbezogene Daten gefunden werden)** | Nichts: Der Aufruf wird abgelehnt, wenn seine Argumente personenbezogene Daten enthalten. |

Wählen Sie für einen Server außerhalb Ihrer Organisation **Schwärzen** oder **Blockieren**. Ist das PII-Modul
ausgeschaltet oder nicht erreichbar, geht der Aufruf unverändert durch.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**„… ist registriert, konnte aber nicht verbunden werden: … Öffnen Sie den Server, um die URL oder die
Zugangsdaten zu korrigieren.“** Der Server ist gespeichert, steht aber auf Fehler. Öffnen Sie ihn, lesen Sie
den Grund und wählen Sie **Bearbeiten**. Häufige Gründe:

- er hat mit `401 Unauthorized` geantwortet: Er braucht Zugangsdaten; fügen Sie sie als Header hinzu, z. B.
  `Authorization: Bearer <token>`;
- unter dieser Adresse gibt es keinen MCP-Endpunkt (`404`): Adressen für Streamable HTTP enden meist auf
  `/mcp`, Adressen für SSE auf `/sse`;
- `Connection refused`: Unter dieser Adresse lauscht nichts, oder der Fadenstack-Server erreicht sie nicht.

**„MCP-Server … ist …“** im Dashboard. Der Server ist fehlerhaft oder getrennt, und seine Tools stehen Chats
nicht zur Verfügung. Öffnen Sie ihn und wählen Sie **Testen**.

**Benutzer sehen im Chat keine Tools.** Prüfen Sie, ob der Server aktiviert ist, ob seine Tools aktiviert sind
und ob das Modell, mit dem sie chatten, Tools aufrufen kann.
