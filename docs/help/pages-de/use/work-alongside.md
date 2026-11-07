# Den Assistenten mit Ihnen zusammenarbeiten lassen

In der Fadenstack-Desktop-App können Sie den Assistenten eine Zeit lang an Orten arbeiten lassen, die Sie nennen, z. B.
in einem Dokument, einem Ordner oder einer App, ohne dass er vor jedem Schritt nachfragt. Sie arbeiten weiter: Er
benutzt nie Ihre Tastatur, Ihre Maus oder das Fenster, in dem Sie gerade sind. Sie wählen die Orte und die Zeit, Sie
sehen, solange er arbeitet, und ein Klick hält ihn an.

## Bevor Sie beginnen {#before-you-start}

- Die Desktop-App, angemeldet (siehe [Eine Desktop-App anmelden](sign-in-an-app.md)).
- Ihr Administrator erlaubt es für den Assistenten. Sonst zeigt die App **Mit mir zusammenarbeiten…** nicht an.
- Etwas, worin er arbeiten kann: ein Dokument, das in Word, Excel oder PowerPoint mit seinem Assistenten offen ist,
  ein Ordner, den Sie unter **Einstellungen → Dateien** hinzugefügt haben, oder ein Fenster, das Sie freigeben.

## Starten {#start-it}

1. Wählen Sie **Mit mir zusammenarbeiten…**: das Personen-Symbol in der Kopfzeile des Chats oder das Menü des Avatars
   (Rechtsklick auf den Avatar).
2. Im Chat erscheint die Karte **Mit Ihnen zusammenarbeiten?**. Unter **Ich arbeite in:** stehen die Orte. Wählen Sie
   neben einem Ort **Entfernen**, um ihn auszulassen.
3. Wählen Sie unter **Wie lange** eine Zeit. 30 Minuten sind vorausgewählt.
4. Lesen Sie, was die Karte ohne Nachfrage erlaubt und wonach er weiterhin fragt, und wählen Sie dann **Zulassen**.
   **Nicht jetzt** erlaubt nichts.

Die Karte zeigt dann **Arbeitet mit Ihnen**. Ohne Nachfrage läuft nichts, bevor der Server Ihrer Organisation es
bestätigt hat.

??? info "Was er ohne Nachfrage darf und wonach er weiterhin fragt"
    An den erlaubten Orten darf er ohne Nachfrage lesen, schreiben und Dinge ändern, wie die Karte sagt. Er fragt
    weiterhin, bevor er etwas löscht, sendet, bezahlt oder veröffentlicht, bevor er anderswo handelt, und bei jedem
    Tool, bei dem Ihr Administrator zuerst gefragt werden möchte. Der Server Ihrer Organisation prüft jeden Schritt:
    Ein Schritt außerhalb dessen, was Sie erlaubt haben, fragt wie immer nach.

## Während er arbeitet {#while-it-works}

| Wo | Was Sie sehen |
|---|---|
| Der Avatar | Die verbleibenden Minuten, unter dem Avatar |
| Die Titelleiste | **Arbeitet mit Ihnen · noch 23 Min.**, mit **Stoppen** |
| Die Menüs des Avatars und des Infobereichs | **Zusammenarbeit beenden** |

- **Stoppen** beendet sie sofort, an allen Orten, und hält die Antwort an, die gerade geschrieben wird.
- **Zwei Minuten vor dem Ende** fragt die App **Weitere 30 Min. mit Ihnen zusammenarbeiten?**. **Weitermachen** gibt
  die Zeit dazu, mit denselben Orten und denselben Regeln; mit **Nicht jetzt** endet sie. **Weitere 30 Min.** im Chat
  tut dasselbe.
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
Administrator kann sie beenden; die Meldung sagt dann, dass der Server sie beendet hat.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was zu tun ist |
|---|---|
| **Noch gibt es nichts, worin ich arbeiten kann** | Öffnen Sie ein Dokument in Word, Excel oder PowerPoint, fügen Sie unter **Einstellungen → Dateien** einen Ordner hinzu oder geben Sie ein Fenster frei, und versuchen Sie es dann erneut. |
| **Der Server hat die Zusammenarbeit nicht erlaubt** | Der Server Ihrer Organisation hat sie abgelehnt; die Meldung sagt, warum. Fragen Sie Ihren Administrator, ob sie erlaubt sein soll. |
| **Der Server war nicht erreichbar, daher wurde nichts erlaubt** | Prüfen Sie Ihre Verbindung und versuchen Sie es erneut. |
| **Nicht zugelassen: Der Server hat es nicht bestätigt** | Es wurde nichts erlaubt. Versuchen Sie es später erneut. |
| **Länger geht es nicht** | Die längste Zeit für diesen Assistenten ist erreicht. Wählen Sie erneut **Mit mir zusammenarbeiten…**. |
| **Stoppen hat den Server nicht erreicht** | Wählen Sie erneut **Stoppen**. Ohne Verbindung endet sie trotzdem zu ihrer Zeit. |
