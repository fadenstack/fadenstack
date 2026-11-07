# Inhalte prüfen {#checking-content}

Prüfen Sie, ob ein Text, ein Bild oder eine Datei die Kennzeichnung trägt, mit der Fadenstack alles versieht,
was die Modelle Ihrer Organisation erzeugen. Sie bekommen ein Ergebnis in einfachen Worten, was gefunden wurde, und bei
Bedarf ein signiertes Ergebnis, das Sie anderen zeigen können.

## Bevor Sie beginnen {#before-you-start}

**Inhalte prüfen** finden Sie im Menü Ihres Avatars oben rechts im Chat. Fehlt der Eintrag dort, wenden Sie sich
an Ihren Administrator.

## Einen Text, ein Bild oder eine Datei prüfen {#check-a-text-a-picture-or-a-file}

1. Öffnen Sie das Menü Ihres Avatars und wählen Sie **Inhalte prüfen**.
2. Wählen Sie **Text** und fügen Sie den Text ein, oder wählen Sie **Bild oder Datei** und dann
   **Datei auswählen**, oder ziehen Sie die Datei in das Feld.
3. Wählen Sie **Prüfen**.

![Die Seite Inhalte prüfen: die Reiter Text und Bild oder Datei, das Feld für den zu prüfenden Text und die Schaltfläche Prüfen, darunter, was geprüft wird](../images/use/check-content.png)

??? info "Was Sie prüfen können"
    - Texte, die Sie einfügen. Zeilenumbrüche und Abstände spielen keine Rolle.
    - Bilder: PNG, JPEG und WebP.
    - Word-, PowerPoint- und Excel-Dokumente, PDF und Textdateien (`.txt`, `.md`, `.html`).

    Bis 20 MB auf einmal. Administratoren finden dieselbe Prüfung auch in der Konsole unter
    **Dienstprogramme → Inhalte prüfen**.

## Das Ergebnis lesen {#read-the-result}

| Sie sehen | Das bedeutet |
|---|---|
| **Als KI-generiert gekennzeichnet** | Eine Kennzeichnung dieser Installation wurde gefunden und bestätigt. |
| **Keine Kennzeichnung gefunden** | Nichts im Inhalt besagt, dass KI ihn erzeugt hat. |
| **Nicht eindeutig** | Es gibt Hinweise, dass KI den Inhalt erzeugt hat, z. B. die Kennzeichnung eines anderen Systems, aber keinen, der sich hier bestätigen lässt. |

![Ein geprüfter Text: Als KI-generiert gekennzeichnet, gefunden als Übereinstimmung mit einem Erzeugungsbeleg und an seinem Text-Wasserzeichen, darunter Signiertes Ergebnis herunterladen und Etwas anderes prüfen](../images/use/check-content-text-ai.png)

![Ein geprüftes Bild: Als KI-generiert gekennzeichnet, gefunden an seinen Content Credentials (C2PA), seinem unsichtbaren Wasserzeichen (TrustMark) und seinen Bildmetadaten (IPTC), alle mit demselben Beleg](../images/use/check-content-picture.png)

Unter **Was gefunden wurde** nennt jeder Fund, wonach gesucht wurde, etwa **Unsichtbares Wasserzeichen
(TrustMark)**, **Signierte Herkunftsangaben (C2PA)**, **Bild-Metadaten (IPTC)**, **Text-Wasserzeichen** oder
**Entspricht einem Erzeugungsbeleg**, und ob er **Deutet auf KI-Erzeugung hin**. Ein gefundener Beleg oder
Nachweis zeigt seine ID.

??? info "Wie ein Text geprüft wird"
    Ein Text wird mit den Belegen und Nachweisen verglichen, die diese Installation über das aufbewahrt, was ihre
    Modelle geschrieben haben. Das gelingt auch, wenn der Text neu formatiert wurde, aber nicht mehr, wenn er
    umformuliert ist. Außerdem wird der Text auf das unsichtbare Text-Wasserzeichen geprüft; dafür braucht es etwa
    200 Wörter oder mehr, und es wird schwächer, wenn der Text stark bearbeitet, übersetzt oder umschrieben wurde.

!!! note "„Keine Kennzeichnung gefunden“ ist kein Beweis"
    **Keine Kennzeichnung gefunden** beweist nicht, dass ein Mensch den Inhalt geschrieben hat: Kennzeichnungen
    lassen sich entfernen, und Inhalte anderer Systeme tragen keine Kennzeichnung dieser Installation.

!!! tip "Die Kennzeichnung erhalten"
    Entfernen Sie die Kennzeichnung nicht aus dem, was Sie aus Fadenstack übernehmen: Erhalten Sie die Metadaten
    und Herkunftsangaben eines Bildes, und schreiben Sie einen Text nicht nur um, damit sein Wasserzeichen
    verschwindet. Veröffentlichen Sie ein Bild, Audio oder Video, das als echt gelten könnte, oder einen von KI
    geschriebenen Text zur Information der Öffentlichkeit, sagen Sie sichtbar, dass eine KI es erzeugt hat, außer
    ein Mensch hat den Text geprüft und jemand verantwortet seine Veröffentlichung; die Regeln Ihrer Organisation
    nennt Ihnen Ihr Administrator.

![Ein geprüfter Text, den ein Mensch geschrieben hat: Keine Kennzeichnung gefunden, das Text-Wasserzeichen nicht gefunden](../images/use/check-content-text-nothing.png)

## Das Ergebnis anderen zeigen {#show-the-result-to-someone-else}

**Signiertes Ergebnis herunterladen** speichert das Ergebnis als JSON-Datei, signiert von dieser Installation.
Wer sie von Ihnen bekommt, kann die Signatur mit den Zertifikaten dieser Installation prüfen.
**Etwas anderes prüfen** leert die Seite.

## Was gespeichert wird {#what-is-kept}

Nichts, was Sie einreichen, wird gespeichert oder protokolliert: Text oder Datei werden geprüft und danach
verworfen.

## Ohne Konto {#without-an-account}

Ihre Organisation kann eine öffentliche Prüfseite anbieten, z. B. unter `https://ai.example.internal/check`. Dort
kann jeder ohne Anmeldung Bilder und Dateien prüfen. Texte werden nur für Personen mit einem Konto geprüft; die
Seite nennt, an wen man sich wenden kann. Eingeschaltet wird sie von Administratoren: siehe
[Kennzeichnung KI-generierter Inhalte](../administer/content-marking.md#the-public-check-page).

![Die öffentliche Prüfseite: Ein Bild oder eine Datei prüfen, mit Datei auswählen, dem Hinweis, dass Texte für Personen mit einem Konto geprüft werden, und was geprüft wird](../images/use/public-check.png)

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **Inhalte prüfen** fehlt in Ihrem Menü | Die Prüfung wird Ihnen nicht angeboten. Wenden Sie sich an Ihren Administrator. |
| **Sie haben keine Berechtigung, Inhalte zu prüfen. Wenden Sie sich an Ihren Administrator.** | Ihrer Rolle fehlt die Berechtigung `provenance:check`. Wenden Sie sich an Ihren Administrator. |
| **Inhalte können hier nicht geprüft werden: Das Modul „Herkunftsnachweis“ ist ausgeschaltet.** | Ihre Organisation hat die Kennzeichnung ausgeschaltet. Wenden Sie sich an Ihren Administrator. |
| **Das ist größer als 20 MB und kann nicht geprüft werden.** | Prüfen Sie eine kleinere Datei oder einen Teil des Textes. |
| **Zu viele Prüfungen in einer Minute. Warten Sie einen Moment und versuchen Sie es dann noch einmal.** | Warten Sie und wählen Sie dann erneut **Prüfen**. |
| **Der Kennzeichnungsdienst ist ausgelastet. Versuchen Sie es gleich noch einmal.** oder **Der Kennzeichnungsdienst ist gerade nicht erreichbar. Versuchen Sie es später noch einmal.** | Versuchen Sie es später noch einmal. Bleibt es so, wenden Sie sich an Ihren Administrator. |
| **Das Ergebnis kann gerade nicht signiert werden: Der Kennzeichnungsdienst hat keinen Signaturschlüssel.** | Das Ergebnis wird angezeigt, lässt sich aber nicht signiert herunterladen. Wenden Sie sich an Ihren Administrator. |
