# Externe Provider {#remote-providers}

Ein externer Provider ist ein Modell, das anderswo läuft: bei einem Cloud-Provider wie OpenAI, Azure OpenAI,
Anthropic, Google, Mistral oder OpenRouter oder auf einem beliebigen Server, der die OpenAI-API spricht. Sobald
er verbunden ist, wird er im Chat und über die API neben Ihren eigenen Modellen angeboten, nach denselben Regeln.

## Bevor Sie beginnen {#before-you-start}

- Ein Konto beim Provider und einen API-Schlüssel aus dessen Konsole.
- Eine Entscheidung darüber, was die Organisation verlassen darf: Lesen Sie zuerst
  [Was die Organisation verlässt](#what-leaves-the-organisation).

## Einen Provider verbinden {#connect-a-provider}

1. Öffnen Sie **LLM → Provider** und wählen Sie **Provider hinzufügen**.

    ![Die Seite LLM-Provider: oben die Suche, Provider hinzufügen und Aktualisieren, in der Liste ein Modell, das Ihr Cluster bereitstellt](../images/administer/providers-list-top.png)

2. Geben Sie unter **Name** einen Namen ein, den Ihre Benutzer wiedererkennen, z. B. `OpenRouter`.
3. Wählen Sie unter **Ziel** die Option **Remote-Endpunkt**.
4. Wählen Sie unter **Anbieter** den Provider. Für OpenAI, Mistral, Groq, DeepSeek und OpenRouter ist die
   **Endpunkt-URL** bereits ausgefüllt; ändern Sie sie nur für einen Proxy. Für gehostete Provider wie
   Anthropic oder Google Gemini lassen Sie sie leer. Für jeden anderen OpenAI-kompatiblen Dienst wählen Sie
   **Andere (OpenAI-kompatibel)** und geben seine Adresse ein, z. B. `https://api.example.com/v1`.
5. Fügen Sie den **API-Schlüssel** ein.
6. Wählen Sie **Verbindung testen**. Dabei wird eine kleine Anfrage gesendet, die Adresse und Schlüssel prüft,
   und die Modelle des Providers werden aufgelistet: „Verbindung erfolgreich — 214 Modell(e) verfügbar.“
7. Wählen Sie unter **Modell** das Modell, das Sie anbieten möchten, oder geben Sie seinen Namen so ein, wie der
   Provider ihn schreibt.

    ![Der Dialog Neuer Provider mit dem Ziel Remote-Endpunkt: Anbieter, Endpunkt-URL, API-Schlüssel, Modell und Verbindung testen, als nächster Schritt Preise (Optional)](../images/administer/provider-add.png)

8. Wählen Sie **Weiter**. Geben Sie unter **Preise (Optional)** ein, was eine Million Eingabe- und
   Ausgabe-Tokens kosten, damit die Konsole die Kosten der Nutzung schätzen kann (siehe
   [Nutzung und Kosten](usage.md)). Lassen Sie beide Felder leer, um diesen Schritt zu überspringen.
9. Wählen Sie **Erstellen**.

Das Modell steht sofort im Chat und über die API zur Verfügung, unter dem Namen, den Sie für das Modell
eingegeben haben.

Ein Provider-Eintrag bietet genau ein Modell an. Um mehrere Modelle desselben Providers anzubieten, legen Sie
für jedes einen eigenen Eintrag an, mit demselben Schlüssel.

!!! note "Höchstens drei"
    Core nimmt insgesamt höchstens drei externe Provider auf, angehaltene eingeschlossen, also höchstens drei
    externe Modelle. Ein vierter wird abgelehnt mit „Resource capacity reached for this deployment. Please upgrade
    your plan to add more providers.“ Ein vLLM-Server, den Sie über Fadenstack routen, zählt ebenfalls als einer
    (siehe [Vorhandene vLLM-Server](../operate/existing-vllm.md)).

## Der Schlüssel des Providers {#the-providers-key}

- Der Schlüssel bleibt auf dem Server. Wer den Chat oder die API nutzt, sieht ihn nie, und die Konsole zeigt
  ihn nicht wieder an.
- Um ihn zu ändern, wählen Sie in der Zeile des Providers **Bearbeiten** und geben unter **API-Schlüssel** den
  neuen ein. Bleibt das Feld leer, bleibt der Schlüssel unverändert.
- Alle, die das Modell nutzen, nutzen diesen Schlüssel: Der Provider rechnet über das Konto der Organisation
  ab, nicht mit der einzelnen Person.

## Einen Provider ändern oder entfernen {#change-or-remove-a-provider}

**Bearbeiten** in der Zeile des Providers ändert **Name**, **Endpunkt-URL**, **API-Schlüssel** und **Modell**
sowie den Schalter **Tools des Gateways anbieten**. Dieser Schalter entscheidet, ob dem Modell die
Uhrzeit, die Tool-Suche und die MCP-Tools des Chats angeboten werden. Schalten Sie ihn für ein Modell oder einen
Router aus, der keine Tools aufruft; beim kostenlosen Router von OpenRouter z. B. werden die Anfragen dann
kürzer, und der Router kann unter allen kostenlosen Modellen wählen.

**Stopp** in der Zeile des Providers beendet das Senden von Anfragen an ihn, ohne ihn zu entfernen; **Start**
sendet sie wieder. **Löschen** entfernt einen gestoppten Provider. Das lässt sich nicht rückgängig machen.

**Preis festlegen** (in der Spalte **Preis $/1 Mio.**) legt den Preis nachträglich fest oder ändert ihn.

Die Liste **Provider** zeigt auch die Modelle, die Ihre Cluster bereitstellen (**Ziel**: **Cluster**). Diese
werden auf der Seite ihrer Bereitstellung verwaltet, nicht hier (siehe [Deployments](deployments.md)).

## Was die Organisation verlässt {#what-leaves-the-organisation}

Eine Anfrage, die ein externer Provider beantwortet, wird an diesen Provider gesendet – über das Internet,
sofern der Provider nicht in Ihrem eigenen Netzwerk steht. Sie enthält, was das Modell zum Antworten braucht:

- die neue Nachricht der Person und die früheren Nachrichten des Chats;
- die zugehörigen Anweisungen: die Anweisungen des Chat-Projekts und die verwendeten [Skills](skills.md);
- den Text von Dateien, die an den Chat angehängt sind, und was Tools zurückgegeben haben, z. B. Passagen aus
  der Wissensdatenbank oder die Antwort eines MCP-Tools;
- wenn **Tools des Gateways anbieten** eingeschaltet ist, die Namen und Beschreibungen der angebotenen
  Tools;
- bei einem [Agenten](agents.md) die Seite oder das Dokument, das der Agent liest, um zu antworten.

Die Antwort kommt auf demselben Weg zurück. Was der Provider davon aufbewahrt und wie lange, regeln seine
eigenen Bedingungen.

**Personenbezogene Daten.** Ist das Modul **Datenschutz (PII)** eingeschaltet, entscheidet die Richtlinie, ob
personenbezogene Daten ersetzt werden, bevor eine Anfrage hinausgeht. Die Richtlinie, mit der Fadenstack
startet, schützt Anfragen an Cloud-Provider: Namen, E-Mail-Adressen, Telefonnummern, Karten- und
Kontonummern und ähnliche Daten werden daraus entfernt. Der Filter liest Englisch: Namen und Orte in deutschem
oder anderssprachigem Text übersieht er oft. Prüfen und ändern Sie sie unter
[Richtlinien und personenbezogene Daten](policies.md).

**Wählen Sie sorgfältig, welches Modell ein Agent nutzt.** Alles, was ein Agent für eine Person liest, geht an
das Modell des Agenten.

## Datenresidenz {#data-residency}

**Observability → Datenresidenz** zeigt für jeden Provider, wo Prompts verarbeitet werden:

| Klasse | Bedeutung |
|---|---|
| **Auf Ihren Maschinen** | Von Fadenstack auf Ihren GPU-Maschinen oder auf diesem Server bereitgestellt. |
| **Ihr Netzwerk** | Eine Adresse in einem privaten Bereich oder in einem Netzwerk, das Sie als intern angegeben haben. |
| **Extern** | Eine bekannte Cloud-API oder eine öffentliche Adresse: Daten verlassen Ihr Netzwerk. |
| **Unbekannt** | Die Adresse lässt sich von diesem Server aus nicht auflösen, oder es ist keine gesetzt. |

Bei jedem Provider steht, warum er so eingeordnet wurde, z. B. „Cloud-API: openrouter“ oder „Registrierte
Maschine: gpu-01“. Die Seite zeigt außerdem, wie viele Daten in jede Richtung geflossen sind, geschätzt anhand
der Tokens, und ein Kennzeichen für die gesamte Umgebung: **Air-Gapped ✓**, **Hybrid** oder **Nur Cloud**.
Solange nicht jeder unbekannte Provider eingeordnet ist, gilt die Umgebung nicht als air-gapped.

![Observability → Datenresidenz: das Kennzeichen Hybrid, die Zahl der lokalen und der externen Provider, die Datenmenge sowie ein Provider auf Ihren Maschinen und ein externer, jeweils mit Wohin Prompts gehen](../images/administer/data-residency.png)

Wenn Fadenstack es nicht erkennen kann oder falsch erkennt, setzen Sie beim Provider **Wohin Prompts gehen**:
**Auf Ihren Maschinen**, **Ihr Netzwerk** oder **Extern**. **Automatisch erkennen** kehrt zur automatischen
Erkennung zurück. Eigene Netzwerke außerhalb der privaten Bereiche, z. B. den öffentlichen Adressbereich Ihres
Rechenzentrums, geben Sie unter **Einstellungen → Allgemein** an: **Internal networks**.

Das [Dashboard](dashboard.md) zeigt dieselben Klassen als **Intern bearbeitet** und unter **KI-Datenverkehr**.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**„Nicht OpenAI-API-kompatibel“.** Die Adresse oder der Schlüssel ist falsch, oder der Dienst spricht unter
dieser Adresse nicht die OpenAI-API. Prüfen Sie, ob die Adresse so endet, wie es die Dokumentation des
Providers angibt, meist auf `/v1`.

**Das Modell erscheint nicht im Chat.** Prüfen Sie, ob der Provider gestoppt ist, und prüfen Sie seinen Namen
unter **Modell** im Dialog **Bearbeiten** des Providers.

**„… antwortet nicht“** im Dashboard. Der Provider hat auf seine letzten Prüfungen nicht geantwortet, und
Anfragen gehen erst wieder an ihn, wenn er antwortet. Prüfen Sie die Statusseite des Providers und den
Internetzugang Ihres Servers.
