# Provisionierung (SCIM) {#provisioning-scim}

Mit der Provisionierung legt Ihr Identitätsanbieter Benutzer hier an, ändert sie und sperrt, wer geht, sobald es
dort geschieht, ohne auf eine Anmeldung zu warten. Jede seiner Gruppen wird hier eine Gruppe, mit denselben
Mitgliedern. Das funktioniert mit Entra ID, Okta und anderen Anbietern, die SCIM 2.0 sprechen, für Anbieter mit
OpenID Connect und SAML, und setzt Fadenstack Enterprise voraus.

![Anmeldeanbieter: Example Corp, mit Provisionierung An](../images/administer/sign-in-providers.png)

## Bevor Sie beginnen {#before-you-start}

- Sie sind als **Superuser** an der Konsole angemeldet.
- Der Anbieter ist unter **Zugriffskontrolle → Anmeldeanbieter** eingerichtet (siehe
  [Anmeldeanbieter](sign-in.md)).
- Der Server ist über HTTPS erreichbar, mit einem Zertifikat, dem der Identitätsanbieter vertraut. Fadenstack
  nimmt die Provisionierung nur über HTTPS an.
- Der Identitätsanbieter erreicht den Server. Entra ID und Okta senden aus ihren Cloud-Diensten: aus dem
  Internet oder über den Agenten des Anbieters in Ihrem Netz (Entra ID: der Bereitstellungs-Agent mit der
  **On-premises SCIM app**).

## Provisionierung einschalten {#switch-provisioning-on}

![Provisionierung für Example Corp: die SCIM-Adresse, die letzten Zeichen des Tokens, die letzte Anfrage des Anbieters und die letzte abgelehnte](../images/administer/sign-in-provisioning.png)

1. Öffnen Sie **Zugriffskontrolle → Anmeldeanbieter** und wählen Sie in der Zeile des Anbieters das Symbol
   **Provisionierung** (zwei Pfeile).
2. Wählen Sie **Provisionierung einschalten**.
3. Kopieren Sie die **SCIM-Adresse** und das **Token**. Das Token wird nur jetzt gezeigt; später zeigt der
   Dialog nur seine letzten vier Zeichen.
4. Tragen Sie beides beim Identitätsanbieter ein (unten) und starten Sie dort die Provisionierung.

Der Dialog zeigt dann die **Letzte Anfrage des Anbieters** und die **Letzte abgelehnte Anfrage**. In der Spalte
**Provisionierung** der Anbieterliste steht für den Anbieter **An**.

??? info "Was Fadenstack von den gesendeten Personen behält"
    Nur, was es braucht. Alles andere bleibt beim Identitätsanbieter und wird hier angenommen und verworfen:
    nie gespeichert, nie in ein Protokoll geschrieben.

    | Der Anbieter sendet | Hier behalten |
    |---|---|
    | Die ID der Person beim Anbieter (`externalId`) | Ja, um die Person wiederzufinden. Nicht angezeigt. |
    | Den Anmeldenamen (`userName`) | Ja |
    | Eine E-Mail-Adresse (die geschäftliche oder primäre) | Ja: die Adresse des Kontos |
    | Aktiv oder nicht (`active`) | Ja: Das Konto ist gesperrt oder nicht |
    | Name und Mitglieder einer Gruppe | Ja: eine Gruppe hier und ihre Mitglieder |
    | Namen, Telefonnummern, Postanschriften, Funktion, Abteilung, Vorgesetzte, Personalnummer, Fotos, weitere Attribute | Nein |

    Ordnen Sie beim Anbieter nur die Attribute der ersten fünf Zeilen zu (unten) und löschen Sie die übrigen
    Zuordnungen.

## Was sich hier ändert {#what-changes-here}

| Beim Identitätsanbieter | Hier |
|---|---|
| Eine Person wird der Anwendung zugewiesen | Sie erhält ein Konto mit den **Rollen neuer Konten** des Anbieters (siehe [Anmeldeanbieter](sign-in.md#who-may-sign-in-and-what-they-get)). Gibt es ein Konto mit ihrer Adresse, wird es verknüpft, außer es gehört einem Superuser oder ein anderer Anmeldeanbieter pflegt es. |
| Adresse oder Anmeldename einer Person ändern sich | Die Adresse des Kontos ändert sich. |
| Eine Person wird entfernt, deaktiviert oder gelöscht | Das Konto wird gesperrt: keine Anmeldung, und keiner ihrer Agenten ist aktiv. Nichts wird gelöscht. |
| Dieselbe Person wird wieder zugewiesen | Dasselbe Konto wird wieder freigegeben. |
| Eine Gruppe wird zugewiesen | Eine Gruppe mit ihrem Namen, **Gepflegt von** dem Anbieter unter **Zugriffskontrolle → Gruppen**. Rollen und Agenten geben Sie ihr dort. |
| Die Mitglieder einer Gruppe ändern sich | Ihre Mitglieder ändern sich. Mitglieder, die ein Administrator hinzugefügt hat, bleiben. |
| Eine Gruppe wird entfernt oder gelöscht | Die Gruppe bleibt, mit den Mitgliedern, die ein Administrator hinzugefügt hat; der Anbieter pflegt sie nicht mehr. |

Superuser werden durch die Provisionierung nie geändert oder gesperrt. Eine Sperre durch einen Administrator
oder nach Tagen ohne Anmeldung hebt der Anbieter nicht auf: Das tut **Wieder freigeben** unter
**Zugriffskontrolle → Benutzer**.

## Die erste Anmeldung {#the-first-sign-in}

Personen, die die Provisionierung angelegt hat, melden sich über den Anbieter an wie alle anderen. Bei ihrer
ersten Anmeldung wird ihr Konto mit der Anmeldung verknüpft, ohne den Nachweis, der von anderen verlangt wird,
die hier schon ein Konto haben:

- über die ID, die der Anbieter bei der Anmeldung sendet, wenn es dieselbe ist, die die Provisionierung
  gesendet hat (Entra ID: **Claim, der die Person benennt** auf `oid` setzen, siehe
  [OpenID Connect](sign-in-oidc.md));
- sonst über ihre E-Mail-Adresse, wenn der Anbieter für sie bürgt.

Danach pflegt die Provisionierung ihre Adresse und ihre Gruppen: Die Anmeldung ändert beides nicht, und die
Gruppenregeln des Anbieters gelten für sie nicht. Damit sich nur diese Personen über den Anbieter anmelden, setzen
Sie in den Einstellungen des Anbieters das Häkchen bei **Nur Personen, die die Provisionierung des Anbieters
angelegt hat, dürfen sich damit anmelden**.

## Entra ID {#entra-id}

Eine Anwendung, die Sie für OpenID Connect unter **App-Registrierungen** angelegt haben, kann nicht
provisionieren. Legen Sie eine zweite Anwendung für die Provisionierung an und weisen Sie ihr dieselben Personen
und Gruppen zu.

1. Öffnen Sie im Microsoft Entra Admin Center **Entra ID → Unternehmensanwendungen → Neue Anwendung → Eigene
   Anwendung erstellen**, geben Sie einen Namen ein, wählen Sie die Option für eine Anwendung, die nicht im
   Katalog ist (Nicht-Katalog), und wählen Sie **Erstellen**. (Für einen SAML-Anbieter können Sie die Anwendung
   verwenden, mit der sich die Benutzer anmelden.)
2. Öffnen Sie ihre Seite **Bereitstellung** und wählen Sie **Neue Konfiguration** (oder **Erste Schritte** und
   als **Bereitstellungsmodus** **Automatisch**).
3. Wählen Sie die Bearer-Authentifizierung, fügen Sie die **SCIM-Adresse** unter **Mandanten-URL** und das
   **Token** unter **Geheimes Token** ein und wählen Sie **Verbindung testen**. Speichern Sie dann.
4. Öffnen Sie unter **Zuordnungen** die Benutzerzuordnung. Behalten Sie `userName`, `active`,
   `emails[type eq "work"].value` und `externalId`; löschen Sie die übrigen Zuordnungen. Stellen Sie als Quelle
   von `externalId` das Attribut `objectId` ein, die ID der Person in Entra ID.
5. Behalten Sie in der Gruppenzuordnung `displayName`, `members` und `externalId`.
6. Wählen Sie unter **Einstellungen**, dass nur zugewiesene Benutzer und Gruppen synchronisiert werden. Weisen Sie
   Personen und Gruppen auf der Seite **Benutzer und Gruppen** der Anwendung zu.
7. Stellen Sie unter **Bereitstellung** den **Bereitstellungsstatus** auf **Ein** und speichern Sie.

Entra ID sendet Änderungen in Zyklen, etwa alle 40 Minuten. Um eine Person sofort zu testen, verwenden Sie die
bedarfsgesteuerte Bereitstellung. Die **Bereitstellungsprotokolle** in Entra ID zeigen, was gesendet wurde und
was dieser Server geantwortet hat.

Die Bezeichnungen folgen der deutschen Oberfläche von Microsoft; in der englischen heißen die beiden Felder
**Tenant URL** und **Secret Token**.

## Okta {#okta}

1. Öffnen Sie in der Okta Admin Console die Anwendung, mit der sich die Benutzer anmelden
   (**Applications → Applications**).
2. Wählen Sie auf **General** unter **App Settings** **Edit**, wählen Sie unter **Provisioning** **SCIM** und
   speichern Sie. Ein Reiter **Provisioning** erscheint.
3. Wählen Sie unter **Provisioning → Integration** **Edit**:
    - **SCIM connector base URL**: die **SCIM-Adresse**.
    - **Unique identifier field for users**: `userName`.
    - **Supported provisioning actions**: **Push New Users**, **Push Profile Updates** und **Push Groups**.
    - **Authentication Mode**: **HTTP Header**, und das **Token** als Bearer-Token, ohne `Bearer` davor.
4. Wählen Sie **Test Connector Configuration** und speichern Sie dann.
5. Wählen Sie unter **Provisioning → To App** **Edit**, schalten Sie **Create Users**, **Update User
   Attributes** und **Deactivate Users** ein und speichern Sie.
6. Entfernen Sie in den Attributzuordnungen, was Fadenstack nicht behält, soweit Okta es zulässt. Was trotzdem
   gesendet wird, wird hier verworfen.
7. Weisen Sie Personen und Gruppen unter **Assignments** zu; senden Sie Gruppen unter **Push Groups**.

## Token ersetzen oder ausschalten {#replace-the-token-or-switch-off}

- **Token ersetzen**: erstellt sofort ein neues Token. Das alte gilt nicht mehr, daher werden die Änderungen des
  Anbieters abgelehnt, bis Sie dort das neue eintragen.
- **Provisionierung ausschalten**: Das Token gilt nicht mehr. Die Personen und Gruppen, die die Provisionierung
  angelegt hat, bleiben, wie sie sind, und die Anmeldung über den Anbieter geht weiter.
- **Den Anbieter löschen** unter **Anmeldeanbieter** beendet auch seine Provisionierung.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was zu tun ist |
|---|---|
| Der Test des Anbieters scheitert mit **401** oder „unauthorized“ | Das Token ist falsch oder wurde ersetzt. Wählen Sie **Token ersetzen** und tragen Sie das neue beim Anbieter ein. |
| Der Test des Anbieters scheitert mit **403**, „Provisioning answers only over HTTPS.“ | Der Anbieter hat `http://` verwendet. Verwenden Sie die **SCIM-Adresse**, wie der Dialog sie zeigt, über HTTPS geöffnet. |
| Der Test des Anbieters scheitert mit **403**, „Provisioning needs an active Fadenstack Enterprise licence.“, oder **Letzte abgelehnte Anfrage** zeigt `403 licence` | Die Lizenz fehlt oder ist abgelaufen; die Admin-Seiten sagen, was davon. Die Konten behalten so lange ihren letzten Stand. |
| Der Anbieter kann gar keine Verbindung aufbauen | Er erreicht den Server nicht oder vertraut seinem Zertifikat nicht. Siehe [Bevor Sie beginnen](#before-you-start). |
| Der Test von Okta meldet, dass keine Benutzer zurückkamen | Es ist noch niemand provisioniert. Speichern Sie und weisen Sie dann Personen zu. |
| **Letzte abgelehnte Anfrage** zeigt `409 UniquenessException` | Zwei Personen beim Anbieter haben denselben Anmeldenamen oder dieselbe Adresse, oder ein anderer Anmeldeanbieter pflegt das Konto mit dieser Adresse. Beheben Sie es beim Anbieter. |
| **Letzte abgelehnte Anfrage** zeigt `403 ForbiddenException` | Die Adresse gehört hier einem Superuser, und Superuser werden nie provisioniert. Weisen Sie diese Person beim Anbieter nicht zu. |
| **Letzte abgelehnte Anfrage** zeigt `400 InvalidFilterException` | Der Anbieter hat auf eine Art gefragt, die dieser Server nicht beantwortet. Ordnen Sie `userName` als eindeutigen Bezeichner zu. |
| **Letzte abgelehnte Anfrage** zeigt **429** | Der Anbieter hat zu viel auf einmal gesendet; er versucht es von selbst erneut. |
