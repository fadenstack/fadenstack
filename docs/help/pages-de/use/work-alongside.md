# Den Assistenten mit Ihnen zusammenarbeiten lassen

In der Fadenstack-Desktop-App können Sie den Assistenten eine Zeit lang an Orten arbeiten lassen, die Sie nennen, z. B.
in einem Dokument, einem Ordner oder einer App, ohne dass er vor jedem Schritt nachfragt. Sie arbeiten weiter: Er
benutzt nie Ihre Tastatur, Ihre Maus oder das Fenster, in dem Sie gerade sind. Sie wählen die Orte und die Zeit, Sie
sehen, solange er arbeitet, und ein Klick hält ihn an.

## Bevor Sie beginnen {#before-you-start}

- Die Desktop-App, angemeldet (siehe [Eine Desktop-App anmelden](sign-in-an-app.md)).
- Ihr Administrator erlaubt es für den Assistenten. Sonst zeigt die App **Mit mir zusammenarbeiten…** nicht an.
- **Mit mir zusammenarbeiten** ist unter **Einstellungen → Arbeitsweise** eingeschaltet. Es ist aus, bis Sie es
  einschalten, und gilt nur für Sie: Wer diesen Computer sonst benutzt, hat eine eigene Einstellung. Solange es aus
  ist, öffnet **Mit mir zusammenarbeiten…** diese Einstellung.
- Etwas, worin er arbeiten kann: ein Dokument, das in Word, Excel oder PowerPoint mit seinem Assistenten offen ist,
  ein Ordner, den Sie unter **Einstellungen → Dateien** hinzugefügt haben, oder ein Fenster, das Sie freigeben.

## Starten {#start-it}

1. Wählen Sie **Mit mir zusammenarbeiten…**: das Personen-Symbol in der Kopfzeile des Chats oder das Menü des Avatars
   (Rechtsklick auf den Avatar).
2. Im Chat erscheint die Karte **Mit Ihnen zusammenarbeiten?**. Unter **Ich arbeite in:** stehen die Orte. Wählen Sie
   neben einem Ort **Entfernen**, um ihn auszulassen.
3. Wählen Sie unter **Wie lange** eine Zeit. 30 Minuten sind vorausgewählt.
4. Lesen Sie, was die Karte ohne Nachfrage erlaubt und wonach er weiterhin fragt, und wählen Sie dann **Zulassen**.
   **Nicht jetzt** erlaubt nichts, und ein neuer Chat bleibt leer.

Die Karte zeigt dann **Arbeitet mit Ihnen**. Ohne Nachfrage läuft nichts, bevor der Server Ihrer Organisation es
bestätigt hat.

??? info "Was er ohne Nachfrage darf und wonach er weiterhin fragt"
    An den erlaubten Orten darf er ohne Nachfrage lesen, schreiben und Dinge ändern, wie die Karte sagt. Er fragt
    weiterhin, bevor er etwas löscht, sendet, bezahlt oder veröffentlicht, bevor er anderswo handelt, und bei jedem
    Tool, bei dem Ihr Administrator zuerst gefragt werden möchte. Der Server Ihrer Organisation prüft jeden Schritt:
    Ein Schritt außerhalb dessen, was Sie erlaubt haben, fragt wie immer nach.

    Führt ein Pfad an einem erlaubten Ort über eine Verknüpfung (eine symbolische Verknüpfung oder unter Windows
    eine Verzeichnisverbindung) an einen anderen Ort, fragt die App vor diesem Schritt und sagt, wohin der Pfad
    wirklich führt.

## Während er arbeitet {#while-it-works}

| Wo | Was Sie sehen |
|---|---|
| Der Avatar | Die verbleibenden Minuten, unter dem Avatar |
| Die Titelleiste | **Arbeitet mit Ihnen · noch 23 Min.**, mit **Stoppen** |
| Die Menüs des Avatars und des Infobereichs | **Zusammenarbeit beenden** |

- **Stoppen** beendet sie sofort, an allen Orten, und hält die Antwort an, die gerade geschrieben wird.
- **Zwei Minuten vor dem Ende** fragt sie einmal, ob sie weitere 30 Minuten mit Ihnen zusammenarbeiten soll: bei
  offenem Chat im Chat mit **Weitere 30 Min.**, sonst in einer Meldung neben dem Avatar, **Weitere 30 Min. mit Ihnen
  zusammenarbeiten?**, mit **Weitermachen** und **Nicht jetzt**. **Weitere 30 Min.** und **Weitermachen** geben die
  Zeit dazu, mit denselben Orten und denselben Regeln; mit **Nicht jetzt** endet sie.
- **Ein neuer Ort:** Geben Sie ein weiteres Fenster frei oder öffnen Sie ein weiteres Dokument mit seinem Assistenten,
  bietet die Karte **Weiter mit Ihnen zusammenarbeiten?** nur diesen Ort an, unter **Außerdem in:**.
- **In Word** unter Windows öffnet sich zu Beginn der Kommentarrand, damit Kommentare, die er später einfügt, die
  Seite unter Ihrem Cursor nicht verschieben. Am Ende ist die Ansicht in Word wieder wie vorher. Die
  Änderungsnachverfolgung schaltet er nie ein oder aus.

??? info "Wie lange sie laufen kann"
    Jeder Assistent hat eine längste Zeit, die Ihr Administrator festlegt, gezählt ab dem Moment, in dem Sie
    **Zulassen** gewählt haben. Lässt sie sich nicht weiter verlängern, sagt die App **Länger geht es nicht**. Um
    weiterzumachen, wählen Sie erneut **Mit mir zusammenarbeiten…** und beantworten die Karte.

## Wenn Sie den Platz verlassen {#when-you-step-away}

- **Sperren Sie den Bildschirm**, pausiert sie; beim Entsperren geht sie weiter. Nach 30 Minuten Sperre endet sie; die
  Meldung wartet, bis Sie zurück sind.
- **Beenden Sie die App**, pausiert sie ebenfalls. Starten Sie die App innerhalb von 30 Minuten wieder, geht sie
  weiter; später endet sie.
- Ihre Zeit läuft während der Pause weiter.

## Wenn sie endet {#when-it-ends}

Wie auch immer sie endet, eine Meldung sagt, wie es lief: **Hat 25 Min. mit Ihnen zusammengearbeitet**, **Wo:** die
Orte, **Ohne Rückfrage ausgeführt:** wie viele Schritte ohne Nachfrage liefen, **Trotzdem nachgefragt:** wie viele
dennoch gefragt haben, und warum sie endete.

Sie können sie auch in den **Einstellungen** des Chats unter **Freigaben** mit **Widerrufen** beenden. Auch Ihr
Administrator kann sie beenden; die Meldung sagt dann, dass der Server sie beendet hat. Schalten Sie **Mit mir
zusammenarbeiten** unter **Einstellungen → Arbeitsweise** aus, endet sie ebenfalls, an allen Orten.

## Bypass in der Desktop-App {#bypass}

Für eine lange Aufgabe, der Sie vertrauen, etwa hunderte Dateien auf dieselbe Weise durchzugehen, können Sie die
Freigaben in einem Chat eine Zeit lang abschalten: **Bypass**. Es steht nur zur Verfügung, wo Ihr Administrator es für
den Assistenten erlaubt; viele Organisationen lassen es ausgeschaltet. Wie es überall funktioniert, steht unter
[Bypass](tools.md#bypass).

1. Wählen Sie den Tool-Modus in der Statusleiste (oder im Chat) und dort **Bypass**.
2. Im Chat erscheint die Karte **Freigaben in diesem Chat abschalten?**. Sie sagt, wonach nicht mehr gefragt wird, was
   weiter fragt, was der Server Ihrer Organisation festhält und **Wie lange**: 30 Minuten sind vorausgewählt.
   Unter **Warum (optional)** können Sie einen Grund angeben; Ihre Administration sieht ihn.
3. Wählen Sie **Bypass einschalten**. Eingabe und Esc schalten es nie ein, und **Nicht jetzt** erlaubt nichts.
4. Ihr Browser öffnet die Fadenstack-Seite **Erneut anmelden**. Melden Sie sich dort erneut an, auch wenn Sie schon
   angemeldet sind, vergleichen Sie den Code mit dem auf der Karte und wählen Sie **Genehmigen**. Die Karte wartet so
   lange; **Seite erneut öffnen** öffnet die Seite noch einmal. Eine Anmeldung gilt 5 Minuten lang auch für Bypass in
   anderen Chats.

Bypass beginnt erst, wenn der Server Ihrer Organisation es bestätigt hat. Sind Sie offline oder lehnt der Server ab,
sagt die Karte **Bypass ist nicht an** und warum, und der Chat bleibt in seinem Modus.

??? info "Wonach nicht mehr gefragt wird, was weiter fragt und was festgehalten wird"
    Nur in diesem Chat führt der Assistent seine Tools aus, ohne Sie vorher zu fragen, auch solche, die löschen, senden
    oder sich nicht rückgängig machen lassen, und solche, die niemand eingestuft hat. Ihre anderen Chats fragen wie
    bisher.

    Weiterhin gefragt wird bei den Regeln **Erst nachfragen** Ihrer Administration und bei Tools, die immer nachfragen.
    Was gesperrt ist, bleibt gesperrt.

    Der Server Ihrer Organisation zeichnet jedes Tool auf, das ohne Nachfrage läuft: welches, wann und wo und, sofern
    Ihre Organisation nicht weniger festhält, was es bekam, mit maskierten personenbezogenen Daten. Ihre Organisation
    kann auch eine kurze Zusammenfassung jedes Ergebnisses aufbewahren. Die Karte sagt, was gilt, und ebenso das Band
    im Chat, solange es läuft.

Solange es läuft:

| Wo | Was Sie sehen |
|---|---|
| Die Kopfzeile des Chats, im Fenster und in der Sprechblase des Avatars | **Bypass an · noch 12 Min.**, was festgehalten wird, und **Stoppen** |
| Der Avatar | Die verbleibenden Minuten, in einem Warnrahmen |
| Die Titelleiste | **Bypass an · noch 12 Min.**, mit **Stoppen** |
| Die Menüs des Avatars und des Infobereichs | Jeder Chat, der in Bypass läuft, mit seiner Restzeit, und **Bypass beenden** |
| Die Liste der Chats | **Bypass · noch 12 Min.** unter jedem Chat, der darin läuft |
| Die **Einstellungen** des Chats, unter **Freigaben** | **Bypass in einem Chat**, die Restzeit und was festgehalten wird, mit **Widerrufen** |

- **Stoppen** beendet es sofort und hält die Antwort an, die gerade geschrieben wird; der Chat kehrt in den Modus
  zurück, den er vorher hatte.
- Es lässt sich nicht verlängern: Ist die Zeit um, endet es. Für mehr Zeit wählen Sie erneut **Bypass** und
  beantworten die Karte.
- Ein anderer Modus im Chat beendet es. Ein neuer Chat oder das Öffnen eines anderen beendet es nicht: Es läuft in
  seinem Chat weiter, und die Titelleiste sagt, in wie vielen Chats Bypass läuft. **Stoppen** in der Titelleiste oder in
  den Menüs beendet alle.
- Das Beenden der App hält es an; seine Zeit läuft weiter. Starten Sie die App innerhalb von 30 Minuten wieder, läuft es
  weiter, ohne neue Karte und ohne Anmeldung. Später, oder wenn Ihre Organisation Bypass inzwischen eingeschränkt hat,
  endet es, und die App sagt, warum.
- Ist es beendet, bietet die Zeile, die das sagt, **Wieder einschalten** an: Es folgen die Karte und die Anmeldung. Der
  Assistent erfährt, dass seine Tools wieder fragen; Bypass selbst einschalten kann er nicht.

??? info "Warum Bypass vorzeitig enden kann"
    Endet Bypass, verschwindet das Band im Chat, und eine Zeile sagt, warum: **Sie haben ihn beendet** (Stoppen,
    Widerrufen, ein anderer Modus oder ein anderes Ihrer Geräte), **Seine Zeit ist um**, **eine Administratorin oder ein
    Administrator hat ihn beendet**, **Ihre Organisation hat Bypass abgeschaltet** oder **Ihre Organisation hat
    eingeschränkt, wer ihn nutzen darf**. Von da an fragen die Freigaben wieder.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was zu tun ist |
|---|---|
| **Noch gibt es nichts, worin ich arbeiten kann** | Öffnen Sie ein Dokument in Word, Excel oder PowerPoint, fügen Sie unter **Einstellungen → Dateien** einen Ordner hinzu oder geben Sie ein Fenster frei, und versuchen Sie es dann erneut. |
| **Der Server hat die Zusammenarbeit nicht erlaubt** | Der Server Ihrer Organisation hat sie abgelehnt; die Meldung sagt, warum. Fragen Sie Ihren Administrator, ob sie erlaubt sein soll. |
| **Der Server war nicht erreichbar, daher wurde nichts erlaubt** | Prüfen Sie Ihre Verbindung und versuchen Sie es erneut. |
| **Nicht zugelassen: Der Server hat es nicht bestätigt** | Es wurde nichts erlaubt. Versuchen Sie es später erneut. |
| **Länger geht es nicht** | Die längste Zeit für diesen Assistenten ist erreicht. Wählen Sie erneut **Mit mir zusammenarbeiten…**. |
| **Stoppen hat den Server nicht erreicht** | Wählen Sie erneut **Stoppen**. Ohne Verbindung endet sie trotzdem zu ihrer Zeit. |
| **Das Ausschalten hat den Server nicht erreicht** | Sie läuft bis zu ihrer Zeit weiter. Die App versucht es erneut, bis der Server antwortet; Sie können auch **Stoppen** wählen. |
| **Bypass ist nicht an: Ihre Organisation erlaubt es für diesen Agenten derzeit nicht** | Ihr Administrator hat Bypass für diesen Assistenten nicht erlaubt oder abgeschaltet. Fragen Sie Ihren Administrator. |
| **Bypass ist nicht an: Der Server war nicht erreichbar** | Prüfen Sie Ihre Verbindung und wählen Sie dann erneut **Bypass**. |
| **Bypass ließ sich nicht beenden** | Wählen Sie erneut **Stoppen**. Ohne Verbindung endet Bypass trotzdem zu seiner Zeit. |
