# Kennzeichnung KI-generierter Inhalte {#content-marking}

Die KI-Verordnung der EU (Art. 50 Abs. 2) verlangt von Anbietern von KI-Systemen, die Text, Bilder, Audio oder
Video erzeugen, ihre Ausgaben maschinenlesbar zu kennzeichnen, damit sie als KI-generiert erkennbar sind. Das
Modul **Herkunftsnachweis** tut das für alles, was die Modelle Ihrer Installation erzeugen, und lässt Benutzer
Inhalte auf diese Kennzeichnung prüfen. Es ist von Anfang an eingeschaltet und muss nicht eingerichtet werden.

Wie Benutzer einen Text, ein Bild oder eine Datei prüfen, steht unter [Inhalte prüfen](../use/checking-content.md).

## Was gekennzeichnet wird {#what-is-marked}

| Inhalt | Was er bekommt |
|---|---|
| Bilder, die Bildmodelle zurückgeben | Ein unsichtbares Wasserzeichen, Bild-Metadaten und signierte Herkunftsangaben (siehe [Bilder](#pictures)) |
| Jede vollständige Textantwort, jedes Modells | Einen signierten Beleg (siehe [Textantworten](#text-answers)) |
| Textantworten Ihrer eigenen Modelle | Zusätzlich ein unsichtbares Text-Wasserzeichen, auf einer Runtime, die es hat |
| Was die Agenten in Dokumente, E-Mails, Webformulare und Dateien schreiben | Eine Kennzeichnung dort und einen signierten Nachweis jeder Einfügung (siehe [Was die Agenten schreiben](#what-the-agents-write)) |

!!! tip "Bitten Sie darum, die Kennzeichnung zu erhalten"
    Die Kennzeichnung nützt nur, solange sie erhalten bleibt. Sagen Sie den Benutzern, dass sie sie nicht
    entfernen sollen: weder die Metadaten oder Herkunftsangaben eines Bildes löschen noch einen Text nur deshalb
    umschreiben, damit sein Wasserzeichen verschwindet. Der Verhaltenskodex der EU zur Kennzeichnung verlangt von
    Anbietern, darauf hinzuweisen.

## Bilder {#pictures}

Jedes Bild, das ein Bildmodell über Fadenstack zurückgibt, wird gekennzeichnet, bevor es den Server verlässt:

- ein **unsichtbares Wasserzeichen** (TrustMark) in den Pixeln, das erneutes Komprimieren und Screenshots
  meist übersteht;
- **Bild-Metadaten** (IPTC), die es als „trained algorithmic media“ ausweisen;
- **signierte Herkunftsangaben** (ein C2PA-Manifest), signiert mit einem Zertifikat der eigenen
  Zertifizierungsstelle Ihrer Installation.

!!! note "Kein Bild geht ungekennzeichnet hinaus"
    Kann der Kennzeichnungsdienst ein Bild nicht kennzeichnen, wird das Bild nicht ausgeliefert: Die Anfrage schlägt mit 503
    `provenance_unavailable` und „The picture could not be marked as AI-generated, so it was not returned.“ fehl.
    Textantworten werden nicht zurückgehalten: Solange der Dienst nicht erreichbar ist, gehen sie ohne Beleg hinaus.

## Textantworten {#text-answers}

**Der Beleg.** Jede vollständige Antwort bekommt einen signierten Beleg: einen Nachweis, dass ein Modell dieser
Installation einen Text mit diesem Hash erzeugt hat. Der Kennzeichnungsdienst speichert den Text nie, nur einen mit einem
Schlüssel gebildeten Hash davon. Eine spätere Prüfung findet den Beleg eines Textes auch dann, wenn Zeilenumbrüche und Abstände geändert
wurden, aber nicht mehr, wenn er umformuliert ist.

**Das Text-Wasserzeichen.** Antworten Ihrer eigenen Modelle tragen außerdem ein unsichtbares Wasserzeichen: ein
Muster in der Wortwahl des Modells, gebildet mit dem geheimen Wasserzeichen-Schlüssel Ihrer Installation. Es lässt
sich in Texten ab etwa 200 Wörtern finden und wird schwächer, wenn der Text stark bearbeitet, übersetzt oder
umschrieben wird. Ein Text, der vor allem wiedergibt, was das Modell bekommen hat (die Zusammenfassung einer Mail,
ein umformulierter Absatz, Namen und Zahlen aus einem Dokument), trägt wenig davon, weil das Modell kaum eigene
Wörter wählt: Ein solcher Text wird über seinen Beleg gefunden, und was die Agenten schreiben, auch über den
Nachweis, den sie dazu ablegen.

Diese Antworten tragen kein Text-Wasserzeichen, und ihr Beleg hält fest, was davon zutrifft:

- kurze Antworten unter 200 Tokens;
- Antworten, die mit Temperatur 0 angefordert wurden (Greedy Decoding: Das Modell nimmt immer das
  wahrscheinlichste Wort);
- Antworten eines Modells auf einer Runtime ohne das Wasserzeichen (siehe
  [Die Runtime entscheidet](#the-runtime-decides));
- Antworten externer Provider. Anthropic kennzeichnet die Antworten seiner Modelle selbst; der Beleg vermerkt das;
- Antworten einer Bereitstellung, für die ein Superuser das Wasserzeichen ausgeschaltet hat.

!!! note "Dieselbe Frage, dieselbe Antwort"
    Mit dem Text-Wasserzeichen ist die Wortwahl des Modells an den Schlüssel gebunden. Wer genau dieselbe Frage
    noch einmal stellt, bekommt deshalb meist genau dieselbe Antwort.

## Die Runtime entscheidet {#the-runtime-decides}

Ob ein Chat-Modell seine Antworten mit dem Wasserzeichen versieht, hängt von der Runtime seines Clusters ab. Auf
der Karte **Laufzeit** eines Clusters gilt: Eine Runtime mit vLLM 0.30 oder neuer hat das Wasserzeichen; für
GB10-Maschinen ist das die Runtime mit vLLM 0.31, die mit vLLM 0.29 hat keines.

- Ein Modell bekommt das Wasserzeichen beim nächsten Start. Ein Wechsel der Runtime startet den Cluster und seine
  Modelle neu (siehe [Cluster](../operate/clusters.md#runtime)).
- Modelle mit spekulativem Decoding (MTP, EAGLE) tragen es ebenfalls; das richtet Fadenstack selbst ein. Es
  wird in weniger ihrer Antworten gefunden, weil die Entwürfe, die das Modell übernimmt, wenig davon tragen.
- Ein Superuser kann das Wasserzeichen für eine einzelne Bereitstellung mit der Engine-Einstellung
  `watermark_config: false` ausschalten (siehe [Engine-Einstellungen](deployments.md#engine-settings)). Die Belege
  ihrer Antworten vermerken dann, dass sie keines tragen.

## Was die Agenten schreiben {#what-the-agents-write}

Die Office-Add-ins, die Chrome-Erweiterung und die Nextcloud-App kennzeichnen, was sie in Dokumente, E-Mails,
Webformulare und Dateien schreiben, jeweils so, wie die Anwendung es zulässt: in Dokumenteigenschaften oder
eigenen XML-Teilen, in Kopfzeilen von E-Mails, in HTML-Attributen, in Datei-Metadaten oder im Front Matter einer
Markdown-Datei. Die Installation bewahrt den signierten Nachweis jeder Einfügung auf.

## Wer Inhalte prüfen kann {#who-can-check-content}

Alle angemeldeten Benutzer können Inhalte prüfen, über das Avatar-Menü im Chat (**Inhalte prüfen**); Sie finden die Prüfung
außerdem in der Konsole unter **Dienstprogramme → Inhalte prüfen**. Dazu ist die Berechtigung `provenance:check`
nötig, die jede integrierte Rolle hat. Geben Sie sie [eigenen Rollen](users.md#roles-of-your-own), deren Benutzer
ebenfalls prüfen sollen.

## Die öffentliche Prüfseite {#the-public-check-page}

Auch Personen ohne Konto können Bilder und Dateien prüfen, sobald Sie die öffentliche Prüfseite anbieten. Sie ist
ausgeschaltet, bis Sie sie einschalten.

1. Öffnen Sie **Einstellungen → Module** und auf der Karte **Herkunftsnachweis** die **Moduleinstellungen**.
2. Schalten Sie unter **Öffentliche Prüfseite** die Option **Öffentliche Prüfseite anbieten** ein. Die Karte zeigt
   die Adresse der Seite, z. B. `https://ai.example.internal/check`.
3. Geben Sie den **Kontakt für Textprüfungen** ein, z. B. `alex@example.com`, und wählen Sie **Speichern**.

Die Seite prüft Bilder und Dateien bis 20 MB, 10 Prüfungen pro Minute und Adresse, und speichert nichts. Texte
lehnt sie ab und nennt stattdessen Ihren Kontakt: Texte werden nur für Personen mit einem Konto geprüft (der
Verhaltenskodex zur KI-Verordnung erlaubt, einzuschränken, wer Texte auf Wasserzeichen prüfen kann). Solange die
Seite eingeschaltet ist, werden auch die Zertifikate veröffentlicht, mit denen sich ein signiertes Ergebnis offline
prüfen lässt.

## Ausschalten und wieder einschalten {#switch-it-off-or-on-again}

1. Öffnen Sie **Einstellungen → Module**.
2. Wählen Sie auf der Karte **Herkunftsnachweis** die Schaltfläche **Stopp**.
3. Lesen Sie, was dann nicht mehr gekennzeichnet wird, setzen Sie das Häkchen bei **Mir ist bewusst, dass erzeugte
   Inhalte dann nicht mehr als KI-generiert gekennzeichnet werden.** und wählen Sie **Abschalten**.

Das wird mit Ihrem Namen und der Uhrzeit im Audit-Protokoll festgehalten. **Start** auf derselben Karte schaltet
das Modul wieder ein.

Auf dem Server tut `faden module disable provenance` dasselbe: Es erklärt, was dann nicht mehr gekennzeichnet wird,
fragt nach einer Bestätigung und hält fest, wer es wann ausgeschaltet hat; `faden up` setzt es danach um, und
`faden module enable provenance` schaltet es wieder ein. Ein Upgrade lässt das Modul, wie es war: ein oder aus.

??? info "Was ausbleibt, solange es aus ist"
    Bilder gehen ungekennzeichnet hinaus, Antworten bekommen keinen Beleg, Modelle, die ab dann starten, starten
    ohne das Text-Wasserzeichen, und **Inhalte prüfen** verschwindet aus Chat und Konsole. Was vorher festgehalten
    wurde, bleibt erhalten.

## Nachweise {#records}

Belege, die Nachweise der Einfügungen und die Berichte der Agenten darüber enthalten Hashes und IDs, nie den
Inhalt. Sie bleiben erhalten, solange sie gebraucht werden. Jede Sicherung enthält sie, mit der Datenbank des
Herkunftsnachweises und ihren Schlüsseln, und eine Wiederherstellung oder ein Upgrade behält sie (siehe
[Sicherungen](../operate/backups.md)).

## Schlüssel {#keys}

Die Schlüssel liegen auf dem Server in der `.env`; der Befehl `faden` verwaltet sie:

| Schlüssel | Wofür | Wie er sich ändert |
|---|---|---|
| Der Signaturschlüssel und sein Zertifikat | Signiert die Herkunftsangaben, die Belege, die Nachweise und die signierten Ergebnisse von Prüfungen | `faden up` und `faden upgrade` stellen einen neuen aus, wenn weniger als 30 Tage übrig sind; `faden secrets rotate` gibt jederzeit einen neuen |
| Die Hash-Schlüssel | Bilden die Hashes, die Belege und Nachweise enthalten | In Versionen aufbewahrt |
| Die Schlüssel des Text-Wasserzeichens | Bilden das Text-Wasserzeichen | In Versionen aufbewahrt |

Das Erneuern eines Schlüssels in Versionen fügt eine Version hinzu, und jede ältere bleibt, damit früher
gekennzeichnete Inhalte weiter geprüft werden können. Ein laufendes Modell behält seinen Wasserzeichen-Schlüssel,
bis es das nächste Mal startet. `faden secrets rotate --only provenance` erneuert nur diese Schlüssel; wie das
Erneuern abläuft, steht unter [Schlüssel erneuern](../operate/rotating-keys.md).

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| Eine Anfrage nach einem Bild schlägt mit 503 `provenance_unavailable` fehl | Der Kennzeichnungsdienst konnte das Bild nicht kennzeichnen. Sehen Sie unter **Einstellungen → Module** auf der Karte **Herkunftsnachweis** nach dem **Kennzeichnungsdienst**. Wer den Server betreibt, prüft `faden status` und `faden logs faden-provenance`. |
| **Kennzeichnungsdienst** zeigt **Läuft nicht** | Solange der Dienst nicht läuft, können Inhalte nicht gekennzeichnet werden. Wer den Server betreibt, startet ihn wieder (`faden up`). |
| **Der Dienst hat das Service-Token abgelehnt** | Backend und Dienst müssen dasselbe Service-Token verwenden. Wenden Sie sich an den Betreiber des Servers. |
| **Inhalte können noch nicht geprüft werden: Für den Kennzeichnungsdienst sind keine Schlüssel eingerichtet.** | In der `.env` fehlen die Schlüssel. `faden up` legt an, was fehlt. |
| Die Antworten eines Modells tragen kein Text-Wasserzeichen | Gehen Sie die Liste unter [Textantworten](#text-answers) durch: die Runtime, ob das Modell seitdem neu gestartet ist, die Länge der Antworten, die Temperatur und ob sie einen vorgegebenen Text wiedergeben. |
