# Ihre Daten und Datenschutz {#your-data-and-privacy}

Fadenstack läuft auf dem eigenen Server Ihrer Organisation. Diese Seite beschreibt, was dort gespeichert wird, wer es sehen kann, wie personenbezogene Daten geschützt werden, wenn Ihre Organisation das eingerichtet hat, und was Ihre Organisation verlässt, wenn ein Modell bei einem externen Provider läuft.

## Was gespeichert wird, und wo {#what-is-kept-and-where}

Auf dem Server Ihrer Organisation:

- Ihr Konto;
- Ihre Chats: die Nachrichten, die Antworten und ihre Titel;
- Ihre Projekte mit ihren Einstellungen;
- die Dateien, die Sie anhängen, und der Text, der daraus gelesen wurde;
- Ihre Skills;
- Nutzungsdaten: wann Sie welches Modell genutzt haben, wie viel Text hinein- und herausging und welche Tools liefen. Sie enthalten nicht, was gesagt wurde.

Bei einer Standardinstallation werden die Nachrichten, die Antworten, die Chat-Titel, die Projektanweisungen und der aus Ihren Dateien gelesene Text verschlüsselt gespeichert; Ihr Administrator kann bestätigen, dass das bei Ihnen so eingerichtet ist. Projektnamen, Dateinamen und die angehängten Dateien selbst werden so gespeichert, wie sie sind. Das Nachdenken eines Modells wird gar nicht gespeichert.

Auf Ihrem eigenen Computer:

- **Der Browser-Agent** speichert seine Unterhaltungen in Ihrem Browser, nicht auf dem Server. Auch Ihre Anmeldung wird dort gespeichert, unverschlüsselt.
- **Die Office-Add-ins** speichern ihre Unterhaltungen verschlüsselt auf Ihrem Computer und Ihre Anmeldung geschützt durch Windows.

Fadenstack bewahrt Ihre Chats auf, bis sie gelöscht werden. Siehe [Löschen](#deleting).

## Wer was sehen kann {#who-can-see-what}

| Wer | Was sie sehen |
|---|---|
| Sie | Ihre Chats, Projekte, Dateien und Skills |
| Kolleginnen und Kollegen | Keinen Ihrer Chats und keine Ihrer Dateien. Skills, die Sie mit ihnen teilen. Die Wissensdatenbank der Organisation teilen sich alle |
| Administratoren, in der Konsole | Dass ein Chat existiert (wer, wann, in welchem Projekt), nicht seinen Titel oder seine Nachrichten. Projektnamen. Namen, Größen und Typen Ihrer Dateien, nicht ihren Inhalt. Ihre Nutzung. Welche Tools liefen. Wie oft personenbezogene Daten gefunden wurden, nicht die Daten selbst. Jeden Skill, auch private |

Die Konsole zeigt Administratoren Ihre Unterhaltungen nicht. Die Personen, die den Server betreiben, haben jedoch den Schlüssel, mit dem die Chats verschlüsselt sind. Nutzen Sie den Chat deshalb wie die E-Mail Ihrer Organisation: privat gegenüber Kolleginnen und Kollegen, aber innerhalb der IT Ihrer Organisation.

## Schutz personenbezogener Daten {#personal-data-protection}

Ihre Organisation kann Fadenstack personenbezogene Daten wie Namen, E-Mail-Adressen, Telefonnummern oder Kontonummern in dem finden lassen, was Sie senden, und sie maskieren lassen, bevor sie ein Modell erreichen. Das legt Ihr Administrator in einer Richtlinie fest: ob es überhaupt aktiv ist, für welche Modelle und wonach gesucht wird. Es ist nicht immer aktiv. Siehe [Richtlinien und personenbezogene Daten](../administer/policies.md).

Wenn es aktiv ist, werden personenbezogene Daten auf eine von zwei Arten maskiert:

- **Schwärzen** (`redact`): Die Daten werden durch eine Bezeichnung wie `<PERSON>` ersetzt, bevor das Modell sie sieht, und kommen nicht zurück. Eine Antwort, die sie erwähnt, zeigt die Bezeichnung.
- **Tokenisierung (reversibel)** (`tokenize_reversible`): Die Daten werden auf dem Weg zum Modell durch einen Platzhalter wie `[PERSON_1]` ersetzt, und der echte Wert wird in der Antwort, die Sie sehen, wieder eingesetzt.

Wenn die Prüfung nicht laufen kann, entscheidet die Richtlinie: Ihre Nachricht wird nicht gesendet, sie wird trotzdem gesendet, oder sie geht stattdessen an eines der eigenen Modelle Ihrer Organisation.

Die Erkennung findet in englischem Text die meisten personenbezogenen Daten, aber nicht alle. In deutschem oder anderssprachigem Text übersieht sie oft Namen und Orte. Fügen Sie nichts ein, was Sie nicht weitergeben dürfen.

### Für einen Chat ansehen und verschärfen {#see-and-tighten-it-for-one-chat}

1. Öffnen Sie mit dem Schraubenschlüssel neben dem Nachrichtenfeld den Tool-Bereich und wählen Sie **Datenschutz**.
2. Die oberste Zeile zeigt **Mandanten-Standardwerte werden verwendet** (die Richtlinie Ihrer Organisation) oder **Sitzungsüberschreibung aktiv** (dieser Chat hat eigene Einstellungen).
3. Um in diesem Chat mehr zu schützen: Schalten Sie **Cloud-Ausgang** ein (für Modelle bei externen Providern) oder **Lokaler Ausgang** (für die eigenen Modelle Ihrer Organisation), wählen Sie eine **Fehleraktion**, oder fügen Sie unter **Erkennungseinstellungen** mit **Entität hinzufügen** eine Art personenbezogener Daten hinzu. Wenn Ihre Organisation es erlaubt, wechselt **Modus** zwischen **Schwärzen** und **Tokenisierung (reversibel)**.
4. Mit **Sitzungsüberschreibung löschen** gelten wieder die Einstellungen Ihrer Organisation.

![Der Tab Datenschutz mit Mandanten-Standardwerte werden verwendet, mit Cloud-Ausgang, Lokaler Ausgang, Modus und Fehleraktion sowie den gesuchten Arten personenbezogener Daten, jede mit einem Schloss, über Entität hinzufügen](../images/use/chat-privacy-tab.png)

Sie können den Schutz nur strenger machen als die Richtlinie Ihrer Organisation, nie lockerer. Einstellungen, die die Richtlinie erzwingt, zeigen ein Schloss.

Im Browser-Agenten und in den Office-Add-ins zeigt ein Hinweis **Personal data protected**, wie viele Werte dem Modell vorenthalten wurden und welcher Art sie waren.

## Modelle bei externen Providern {#models-at-outside-providers}

Ihr Administrator kann neben den eigenen Modellen Ihrer Organisation auch Modelle anbieten, die bei einem externen Provider laufen, etwa bei OpenAI oder Azure. Wenn Sie eines davon wählen, geht alles, was das Modell für die Antwort braucht, an diesen Provider: Ihre Nachricht, die früheren Nachrichten des Chats, der Text Ihrer angehängten Dateien, Projekt- und Skill-Anweisungen, Tool-Ergebnisse und Abschnitte aus der Wissensdatenbank. Personenbezogene Daten werden vorher maskiert, wenn die Richtlinie das vorsieht. Es gelten dann die eigenen Bedingungen des Providers.

Die Modellliste zeigt nicht immer, wo ein Modell läuft. Wenn das für das, was Sie schreiben, wichtig ist, fragen Sie Ihren Administrator, oder wählen Sie ein Modell, von dem Sie wissen, dass es innerhalb Ihrer Organisation läuft. Siehe [Externe Provider](../administer/providers.md).

## Tools und die Wissensdatenbank {#tools-and-the-knowledge-base}

- Ein Tool, das Ihr Administrator angebunden hat, erhält, was das Modell ihm übergibt, oft einen Suchbegriff oder Text aus Ihrer Unterhaltung. Ihr Administrator legt pro Dienst fest, wie personenbezogene Daten in diesen Aufrufen behandelt werden. Siehe [Tools im Chat](tools.md).
- Alles in der Wissensdatenbank der Organisation kann im Chat jeder Person auftauchen. Ihre angehängten Dateien kommen nicht hinein. Siehe [Wissen](knowledge.md).

## Löschen {#deleting}

- **Ein Chat**, den Sie löschen, verschwindet sofort aus Ihrer Liste. Vom Server wird er dabei nicht gelöscht: Seine Nachrichten und Dateien bleiben dort, bis ein Administrator den Chat löscht.
- **Ein Projekt**, das Sie löschen, wird mit allen seinen Chats gelöscht.
- **Im Browser-Agenten** entfernt das Löschen eines Chats in **Chat history** ihn aus Ihrem Browser.

Wenn etwas endgültig gelöscht werden muss, z. B. personenbezogene Daten, die Sie versehentlich eingefügt haben, wenden Sie sich an Ihren Administrator.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**PII service unavailable and fail_action=block.** Die Prüfung auf personenbezogene Daten konnte nicht laufen, und die Richtlinie Ihrer Organisation sieht vor, ohne sie keine Nachrichten zu senden. Versuchen Sie es später erneut; wenn es immer wieder passiert, wenden Sie sich an Ihren Administrator.

**Eine Antwort zeigt `<PERSON>` oder `[PERSON_1]`, wo ein Name stehen sollte.** Der Name wurde maskiert, bevor er das Modell erreichte. Bei **Schwärzen** ist das so vorgesehen. Bei **Tokenisierung (reversibel)** sollte der Name wieder erscheinen; wenn nicht, wenden Sie sich an Ihren Administrator.

**Ihr Administrator hat nicht erlaubt, den PII-Modus für diesen Chat zu ändern.** Ihre Organisation legt den Modus fest. Die anderen Einstellungen unter **Datenschutz** können Sie möglicherweise trotzdem ändern.

**… wird nicht angewendet. Wählen Sie den Modus erneut, um ihn anzuwenden.** Der Modus, den Sie zuvor gewählt haben, wurde nicht wirksam. Wählen Sie ihn unter **Modus** erneut.
