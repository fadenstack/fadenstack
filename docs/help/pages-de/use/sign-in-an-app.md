# Eine Desktop-App anmelden {#sign-in-a-desktop-app}

Manche Apps auf Ihrem Computer melden sich mit einem Code statt mit Ihrem Passwort bei Fadenstack an: Die App
zeigt einen Code, und Sie genehmigen ihn in Ihrem Browser, wo Sie sich wie gewohnt bei Fadenstack anmelden. Ihr
Passwort und die Anmeldung Ihrer Organisation bleiben im Browser; die App sieht sie nie.

## Bevor Sie beginnen {#before-you-start}

- Sie brauchen ein Fadenstack-Konto und Zugriff auf den Agenten, mit dem die App arbeitet. Den gibt Ihnen Ihr
  Administrator (siehe [Agenten und Add-ins](../administer/agents.md)).
- Halten Sie die App auf Ihrem Computer geöffnet, und Ihren Browser auf demselben Computer oder auf Ihrem Telefon.

## Die Anmeldung genehmigen {#approve-the-sign-in}

1. Starten Sie in der App die Anmeldung. Sie zeigt einen Code aus acht Buchstaben, z. B. `BDWP-HTQX`, und öffnet
   die Seite **Eine App anmelden** in Ihrem Browser. Kann sie den Browser nicht öffnen, öffnen Sie die Adresse
   Ihres Servers mit `/pair` dahinter, z. B. `https://ai.example.internal/pair`, und tippen Sie den Code dort ein.
2. Ist der Browser noch nicht bei Fadenstack angemeldet, melden Sie sich wie gewohnt an: mit Ihrem Passwort oder
   mit der Anmeldung Ihrer Organisation. Danach kommen Sie zu **Eine App anmelden** zurück, der Code ist noch
   eingetragen.
3. Prüfen Sie den **Code** und wählen Sie **Weiter**.
4. Prüfen Sie, was angemeldet werden möchte (siehe
   [Was Sie vor dem Genehmigen prüfen](#what-to-check-before-you-approve)), und wählen Sie **Genehmigen**.

Sie sehen **Angemeldet. Sie können zur App zurückkehren.** Die App schließt die Anmeldung selbst ab, innerhalb
weniger Sekunden.

Zum Ablehnen wählen Sie **Ablehnen**. Sie sehen **Abgelehnt. Die App wurde nicht angemeldet.**, und die App meldet,
dass die Anmeldung abgelehnt wurde.

Die Seite oder den Link zu öffnen, den die App öffnet, genehmigt nichts: Das tut nur **Genehmigen**.

## Was Sie vor dem Genehmigen prüfen {#what-to-check-before-you-approve}

Die Seite fragt, ob *der Agent* auf *dem Gerät* angemeldet werden soll, und zeigt:

| Sie sehen | Was Sie prüfen |
|---|---|
| **Prüfen Sie, ob die App auf Ihrem Computer** … **anzeigt** mit dem Code | Der Code muss der in der App vor Ihnen sein. Ist er es nicht, wählen Sie **Ablehnen**. |
| **App** | Name und Version der App, nach Angabe der App. Eine App kann hier alles angeben: ein Hinweis, kein Beweis. |
| **Gerät** | Der Name, den die App dem Computer gegeben hat, und sein System, ebenfalls nach Angabe der App. |
| **Angefragt** | Die Adresse, von der die Anfrage kam, und wann. |
| **Diese Anfrage kam aus einem anderen Netzwerk als Ihrem.** | Die App hat von einer anderen Netzwerkadresse aus gefragt als Ihr Browser. Das ist normal, wenn Ihr Browser auf Ihrem Telefon oder über eine andere Verbindung läuft; haben Sie es nicht erwartet, wählen Sie **Ablehnen**. |
| **Bleibt angemeldet** | Wie lange die App höchstens angemeldet bleibt, und nach welcher Zeit ohne Nutzung die Anmeldung früher endet. Beides legt Ihre Organisation fest. |

!!! warning "Genehmigen Sie nur, was Sie selbst gestartet haben"
    Genehmigen Sie nur, wenn Sie die Anmeldung gerade eben selbst gestartet haben, auf Ihrem eigenen Computer.
    Fadenstack bittet Sie nie, einen Code zu genehmigen, den Ihnen jemand per E-Mail, Chat oder Telefon geschickt
    hat. Eine App, die Sie genehmigen, arbeitet in Ihrem Namen, mit Ihrem Zugriff auf den Agenten.

## Grenzen {#limits}

- Ein Code gilt **10 Minuten** und einmal. Für einen neuen starten Sie die Anmeldung in der App erneut.
- Falsche oder abgelaufene Codes werden gezählt: **5 pro Person** und **20 pro Netzwerkadresse** in
  **10 Minuten**. Darüber hinaus sagt die Seite **Zu viele Versuche** und wann Sie es erneut versuchen können. Ihr
  Konto wird nicht gesperrt: Nur das Eingeben von Codes wartet, die Anmeldung bei Fadenstack funktioniert wie
  zuvor.

## Die Anmeldung sehen und beenden {#see-and-end-the-sign-in}

- **In der Konsole:** Unter **Angemeldete Apps** in **Mein Profil** sehen Sie jede App und jedes Gerät, auf dem Sie
  angemeldet sind, und beenden eine mit **Abmelden** (siehe [Ihr Konto](account.md#signed-in-apps)).
- **In der App:** Wenn Sie sich in der App abmelden, endet ihre Anmeldung auch am Server.
- **Überall auf einmal:** Eine Änderung Ihres Passworts beendet alle Ihre Anmeldungen, auch die der Apps auf
  Ihren Geräten (siehe [Ihr Konto](account.md#change-your-password)).
- **Ein Administrator oder die Betreuer des Agenten** sehen jedes bei einem Agenten angemeldete Gerät unter
  **Angemeldete Geräte** auf dem Tab **Aktivität** des Agenten und können dort eines abmelden (siehe
  [Agenten und Add-ins](../administer/agents.md)). Wenden Sie sich an sie, wenn Sie einen angemeldeten Computer
  verloren haben.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **Für diesen Code wartet keine Anmeldung.** | Vergleichen Sie den Code Buchstabe für Buchstabe mit der App. Ein Code gilt 10 Minuten: Ist er abgelaufen, starten Sie die Anmeldung in der App erneut, um einen neuen zu erhalten. |
| **Diese Anmeldung wurde bereits genehmigt oder abgelehnt.** | Jeder Code gilt nur einmal. Starten Sie die Anmeldung in der App erneut. |
| **Zu viele Versuche.** | Warten Sie die Zeit ab, die die Seite nennt, und versuchen Sie es dann erneut. Trifft es viele Personen auf einmal, erreichen sie den Server vielleicht über eine einzige Adresse; sagen Sie es Ihrem Administrator. |
| **Dieser Agent steht Ihnen nicht zur Verfügung. Wenden Sie sich an Ihren Administrator.** | Sie haben keinen Zugriff auf den Agenten, den die App anfragt, oder er wurde stillgelegt. Ihr Administrator kann Ihnen Zugriff geben. |
| Sie landen auf der Anmeldeseite | Ihre Anmeldung im Browser ist abgelaufen. Melden Sie sich erneut an: Sie kommen mit dem Code zur Seite zurück. |
