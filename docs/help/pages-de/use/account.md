# Ihr Konto {#your-account}

Über Ihr Konto erkennt Fadenstack Sie: Ihre Anmeldung, Ihre Chats, Ihre Skills und die API-Tokens, die Ihre Skripte und Anwendungen verwenden. Diese Seite behandelt Ihr Profil, Ihr Passwort, Sprache und Aussehen der Konsole, wie lange eine Anmeldung gilt und persönliche API-Tokens.

![Mein Profil mit Kontoinformationen, Passwort ändern und dem Anfang von Darstellung](../images/use/profile.png)

## Ihr Profil {#your-profile}

1. Wählen Sie in der oberen Leiste Ihren Avatar.
2. Wählen Sie **Profil verwalten**.

Die Seite **Mein Profil** öffnet sich mit:

| Abschnitt | Was darin steht |
|---|---|
| **Kontoinformationen** | Ihre **E-Mail**, Ihre **Benutzer-ID** und Ihr **Status** |
| **Passwort ändern** | Siehe [Ihr Passwort ändern](#change-your-password) |
| **Darstellung** | Die **Akzentfarbe** (**Frost**, **Graphite** oder **Aurora**) und das **Design** (**Hell** oder **Dunkel**). Beides wird in diesem Browser gespeichert. |
| **Angemeldete Apps** | Die Apps und Geräte, auf denen Sie angemeldet sind (siehe [Angemeldete Apps](#signed-in-apps)) |
| **API-Token** | Ihre persönlichen API-Tokens (siehe [API-Tokens](#api-tokens)) |

Ihre E-Mail-Adresse und was Sie tun dürfen, legt Ihr Administrator fest. Siehe [Benutzer, Gruppen und Rollen](../administer/users.md).

## Ihr Passwort ändern {#change-your-password}

1. Geben Sie in **Mein Profil** unter **Passwort ändern** Ihr **Aktuelles Passwort** ein.
2. Geben Sie das **Neue Passwort** zweimal ein. Es muss mindestens 8 Zeichen lang sein.
3. Wählen Sie **Passwort ändern**.

Sie sehen **Passwort erfolgreich geändert. Ihre anderen Anmeldungen wurden beendet.** Jeder andere Browser und jede andere App, in der Sie angemeldet sind, muss sich erneut anmelden; dieser Browser bleibt angemeldet. Ihre API-Tokens funktionieren weiter: Widerrufen Sie sie weiter unten, falls jemand anderes eines haben könnte.

## Passwort vergessen {#forgot-your-password}

1. Wählen Sie auf der Anmeldeseite **Passwort vergessen?**.
2. Geben Sie Ihre E-Mail-Adresse ein.
3. Wählen Sie **Reset-Link senden**.

Per E-Mail erhalten Sie einen Link, mit dem Sie ein neues Passwort festlegen. Wenn nichts ankommt, bitten Sie Ihren Administrator, ein neues Passwort für Sie festzulegen.

Die Anmeldeseite bietet **Passwort vergessen?** nur an, wenn der Server Ihrer Organisation E-Mails versendet. Sonst steht dort **Passwort vergessen? Die Administration kann ein neues setzen.**: Wenden Sie sich an Ihren Administrator. Sie erhalten einen Link, mit dem Sie ein neues Passwort festlegen; er gilt einmal und nur eine Zeit lang. Öffnen Sie ihn, wählen Sie das neue Passwort und melden Sie sich dann damit an.

## Sprache und Design {#language-and-theme}

| Um zu ändern | Verwenden Sie |
|---|---|
| **Sprache** | Die Schaltfläche mit dem Übersetzungssymbol in der oberen Leiste. Verfügbar sind Englisch, Deutsch, Spanisch, Französisch und Chinesisch. Die Auswahl ändert die Beschriftungen der Konsole, nicht die Sprache, in der das Modell antwortet. Sie wird für Ihr Konto gespeichert: Die Konsole auf Ihren anderen Computern, der Browser-Agent und die Office-Add-ins folgen ihr. |
| **Hell oder dunkel** | Die Schaltfläche mit Sonne oder Mond in der oberen Leiste. |

Eine Sprache, die Sie in den Einstellungen des Browser-Agenten oder eines Office-Add-ins wählen, wird genauso gespeichert, und die Konsole folgt ihr ebenfalls. In diesen Bereichen kehrt **Automatisch** zur Sprache des Browsers oder von Office zurück. Hat Ihre Organisation eine Sprache festgelegt, zeigen die Bereiche sie mit **Von Ihrer Organisation festgelegt** an, und Sie können sie dort nicht ändern.

## Wie lange eine Anmeldung gilt {#how-long-a-sign-in-lasts}

Eine Anmeldung gilt sieben Tage, sofern Ihre Organisation keine andere Dauer festgelegt hat. Danach werden Sie gebeten, sich erneut anzumelden. Um sie früher zu beenden, wählen Sie Ihren Avatar und dann **Abmelden**: Diese Anmeldung ist sofort beendet, auch für jeden, der sie kopiert hat. Eine Passwortänderung oder ein Administrator, der Ihr Konto sperrt oder Ihnen eine Rolle entzieht, beendet alle Ihre Anmeldungen. Melden Sie sich an jedem Computer ab, den auch andere nutzen.

Hat Ihre Organisation ihre eigene Anmeldung angebunden, bietet die Anmeldeseite sie an: **Anmelden mit** und
den Namen Ihrer Organisation, oder eine Auswahl zwischen **Fadenstack-Konto** und dem Verzeichnis Ihrer
Organisation, wo Sie Ihren **Kontonamen** und Ihr gewohntes Passwort eingeben. Eine so begonnene Anmeldung kann
kürzer als sieben Tage gelten, wie Ihre Organisation es festgelegt hat.

## Verknüpfte Anmeldungen {#linked-sign-ins}

![Verknüpfte Anmeldungen in Mein Profil: Example Corp mit Trennen, und dass auch Ihr Passwort Sie anmeldet](../images/use/profile-linked-sign-ins.png)

**Mein Profil** zeigt unter **Verknüpfte Anmeldungen** die Anmeldeanbieter, mit denen Sie sich anmelden, und ob
auch Ihr Passwort Sie anmeldet. Der Abschnitt erscheint, wenn Ihre Organisation ihre eigene Anmeldung angebunden
hat.

- **Anbieter verknüpfen**: Wählen Sie ihn aus und melden Sie sich dort wie gewohnt an. Sie kommen zu **Mein
  Profil** zurück und können sich von da an damit anmelden. Bei einem Verzeichnis geben Sie stattdessen Ihren
  **Kontonamen** und dessen Passwort ein.
- **Trennen**: Sie können sich mit diesem Anbieter nicht mehr anmelden, bis Sie ihn wieder verknüpfen. Die
  Schaltfläche ist aus, wenn es Ihre einzige Art ist, sich anzumelden: Sie würden sich aussperren. Bitten Sie
  Ihren Administrator zuerst um einen Link zum Zurücksetzen des Passworts, wenn Sie sich stattdessen mit einem
  Passwort anmelden möchten.

Eine Anmeldung, die hier schon zu einem anderen Konto gehört, wird nicht mit Ihrem verknüpft.

## Angemeldete Apps {#signed-in-apps}

**Mein Profil** zeigt unter **Angemeldete Apps** jede App und jedes Gerät, auf dem Sie angemeldet sind: Die
Desktop-App, die Office-Add-ins und die Browser-Erweiterung melden sich jeweils einmal pro Gerät an. Zu jeder sehen
Sie die **App**, das **Gerät**, wann Sie **Angemeldet** wurden und wann sie **Zuletzt verwendet** wurde.

Um eine zu beenden, wählen Sie daneben **Abmelden** und bestätigen Sie. Die App auf diesem Gerät muss dann erneut
angemeldet werden (siehe [Eine Desktop-App anmelden](sign-in-an-app.md)). Beenden Sie die Anmeldung eines Computers,
den Sie verloren haben oder nicht mehr nutzen, und jede, die Sie nicht kennen.

## API-Tokens {#api-tokens}

Mit einem API-Token kann ein Skript oder eine Anwendung die Modelle Ihrer Organisation in Ihrem Namen nutzen, über die API, die für Chat, Embeddings und Rerank wie die von OpenAI funktioniert. Das Token steht für Sie: Was darüber gesendet wird, zählt als Ihre Nutzung und unterliegt denselben Regeln wie Ihre Chats. Für den Chat, den Browser-Agenten oder die Office-Add-ins brauchen Sie kein Token.

### Ein Token erstellen {#create-a-token}

1. Gehen Sie in **Mein Profil** zu **API-Token**.
2. Geben Sie unter **Name** an, welches Skript oder welche Anwendung das Token verwenden wird, z. B. `Berichtsskript`.
3. Wählen Sie den **Token-Ablauf**.
4. Wählen Sie **Token erzeugen**.
5. Kopieren Sie das Token mit der Kopier-Schaltfläche und bewahren Sie es sicher auf.

??? info "Mehr zu den Feldern"
    | Feld | Gut zu wissen |
    |---|---|
    | **Name** | Benennen Sie das Token nach dem, was es verwenden wird, damit Sie später wissen, welches Token Sie widerrufen müssen. |
    | **Token-Ablauf** | **1 Stunde**, **24 Stunden**, **7 Tage**, **30 Tage**, **90 Tage** oder **1 Jahr**. Jedes Token läuft ab; kürzer ist sicherer, und Sie können jederzeit ein neues erstellen. |

!!! warning "Nur einmal angezeigt"
    Das Token wird nur dieses eine Mal angezeigt. Sobald Sie die Seite verlassen, lässt es sich nicht mehr anzeigen; erstellen Sie ein neues, wenn Sie es verlieren. Wer das Token hat, kann die Modelle in Ihrem Namen nutzen.

Die Anwendung braucht außerdem die Adresse der API, die Ihnen Ihr Administrator nennen kann. Wie Sie die API aufrufen, steht auf der Website [Developers](https://developers.fadenstack.com/).

### Ihre Tokens ansehen und widerrufen {#see-and-revoke-your-tokens}

**Ihre Tokens** listet jedes Token mit seinem Namen, seinem Erstellungsdatum und seinem Ablaufdatum auf. Abgelaufene und widerrufene Tokens sind mit **Abgelaufen** oder **Widerrufen** markiert.

1. Wählen Sie neben dem Token **Widerrufen**.
2. Bestätigen Sie mit **Widerrufen**.

Anwendungen, die das Token nutzen, funktionieren sofort nicht mehr. Widerrufen Sie ein Token, sobald es nicht mehr gebraucht wird oder wenn es jemand anderes gesehen haben könnte.

Jedes Token zeigt, wann es zuletzt genutzt wurde, von welcher Adresse und mit welcher Anwendung; **Nutzungen** listet seine letzten Nutzungen. Jede Nutzung wird festgehalten, und Ihre Administratoren sehen sie auch: Eine Nutzung, die Sie nicht kennen, ist ein Grund, das Token zu widerrufen.

??? info "Wann ein Token sonst nicht mehr funktioniert"
    Ihr Administrator kann Ihre Tokens ebenfalls widerrufen. Wenn Ihr Konto deaktiviert wird, funktionieren auch Ihre Tokens nicht mehr.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| Sie werden zur Anmeldeseite zurückgeleitet | Ihre Anmeldung ist abgelaufen. Melden Sie sich erneut an. Wenn Ihr Passwort nicht mehr akzeptiert wird, wenden Sie sich an Ihren Administrator: Ihr Konto wurde möglicherweise geändert oder deaktiviert. |
| **Neue Passwörter stimmen nicht überein.** oder **Passwort muss mindestens 8 Zeichen lang sein.** | Geben Sie das neue Passwort erneut ein. Es darf auch nicht Ihre E-Mail-Adresse sein. |
| **Too many attempts from this address. Wait a minute, then try again.** | Der Server zählt Anmelde- und Passwort-Zurücksetzungsversuche pro Adresse. Warten Sie eine Minute. Trifft es viele Personen gleichzeitig, erreichen sie den Server womöglich alle über eine Adresse (einen Proxy); sagen Sie Ihrem Administrator Bescheid. |
| Eine Anwendung meldet, dass ihr Token widerrufen oder abgelaufen ist | Erstellen Sie ein neues Token und geben Sie es der Anwendung. |
