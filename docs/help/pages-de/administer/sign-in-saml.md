# SAML {#saml}

Verbinden Sie einen Identitätsanbieter, der SAML 2.0 spricht, etwa AD FS, Shibboleth oder Entra ID. Die
Benutzer wählen auf der Anmeldeseite **Anmelden mit** und den Namen des Anbieters, melden sich dort an und
kommen angemeldet zurück. Was für alle Anbieter gilt, steht unter [Anmeldeanbieter](sign-in.md).

## Bevor Sie beginnen {#before-you-start}

- Sie dürfen beim Identitätsanbieter einen Dienstanbieter hinzufügen (auch vertrauende Seite oder
  Unternehmensanwendung genannt).
- Sie haben die Metadaten des Identitätsanbieters: eine Adresse, unter der er sie veröffentlicht, oder die
  XML-Datei.

## Fadenstack beim Identitätsanbieter eintragen {#register-fadenstack-at-the-identity-provider}

1. Öffnen Sie in der Konsole **Zugriffskontrolle → Anmeldeanbieter**, wählen Sie **Anbieter hinzufügen** und
   unter **Art** **SAML 2.0**.
2. Geben Sie den **Namen** ein und prüfen Sie den **Schlüssel**. Der Dialog zeigt nun drei Werte, jeden mit
   einem Kopiersymbol:

    | In der Konsole | Beim Identitätsanbieter |
    |---|---|
    | **Metadaten-Adresse** | Die Metadaten des Dienstanbieters. Die meisten Identitätsanbieter lesen alles daraus. |
    | **Entity-ID** | Die Kennung (Entity-ID) des Dienstanbieters. |
    | **Assertion Consumer Service (ACS)** | Die Antwort-URL; Antworten kommen per HTTP POST dorthin. |

3. Fügen Sie beim Identitätsanbieter den Dienstanbieter mit der **Metadaten-Adresse** hinzu, oder mit
   **Entity-ID** und **ACS**-Adresse.
4. Lassen Sie ihn senden:
    - eine dauerhafte NameID (persistent), oder das Attribut `subject-id` oder `pairwise-id`: einen Wert, der
      für eine Person gleich bleibt. Eine vorübergehende NameID (transient) wird abgelehnt;
    - die E-Mail-Adresse (`mail`, `email` oder der E-Mail-Claim der Identitätsanbieter von Microsoft);
    - wahlweise den Anzeigenamen und die Gruppen (`displayName`, `groups` oder `memberOf`).

## Die Felder des Identitätsanbieters ausfüllen {#fill-in-the-identity-providers-fields}

| Feld | Was hineingehört |
|---|---|
| **Metadaten des Identitätsanbieters** | **Von seiner Adresse**: die Adresse, unter der er seine Metadaten veröffentlicht (`https://`). **Eingefügt**: das XML seiner Metadaten. |
| **Seine E-Mail-Adressen sind geprüft** | Setzen Sie das Häkchen, wenn der Identitätsanbieter der Ihrer Organisation ist: SAML sagt nicht, ob eine Adresse geprüft ist. Sonst tragen Sie Ihre Domains unter **Erlaubte E-Mail-Domains** ein. |
| **Anfragen an den Identitätsanbieter signieren** | Nur, wenn der Identitätsanbieter signierte Anfragen verlangt. Das Zertifikat steht in der **Metadaten-Adresse**. |

Legen Sie dann fest, wer sich anmelden darf (siehe
[Anmeldeanbieter](sign-in.md#who-may-sign-in-and-what-they-get)), und wählen Sie **Anbieter hinzufügen**.

!!! note
    Die Assertion jeder Antwort muss vom Identitätsanbieter signiert sein; unsignierte werden abgelehnt.

??? info "Entra ID"
    1. Öffnen Sie im Microsoft Entra Admin Center **Unternehmensanwendungen**, wählen Sie **Neue Anwendung**,
       dann **Eigene Anwendung erstellen**, und als Methode für das einmalige Anmelden SAML.
    2. Tragen Sie unter **SAML-Basiskonfiguration** die **Entity-ID** als Bezeichner und die **ACS**-Adresse
       als Antwort-URL ein.
    3. Wählen Sie in der Konsole **Von seiner Adresse** und fügen Sie die **App-Verbundmetadaten-URL** ein.

    Entra ID sendet Gruppen mit ihrer Objekt-ID: Gruppenregeln nennen die ID, nicht den Namen der Gruppe.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was zu tun ist |
|---|---|
| **„Der Anmeldedienst ist gerade nicht erreichbar.“** | Das Anmeldemodul konnte die Metadaten des Identitätsanbieters nicht von seiner Adresse lesen. Prüfen Sie, ob der Server sie über HTTPS erreicht, oder fügen Sie stattdessen das XML ein. |
| Der Identitätsanbieter meldet, Antwort-URL oder Entity seien unbekannt | Tragen Sie **Entity-ID** und **ACS**-Adresse genau so ein, wie der Dialog sie zeigt. |
| **„Die Anmeldung hat nicht geklappt.“** nach dem Identitätsanbieter | Die Antwort war unsigniert, für einen anderen Dienstanbieter, zu alt, oder hatte eine vorübergehende NameID. Prüfen Sie die Einstellungen des Identitätsanbieters für diesen Dienstanbieter. |
| **„Der Anbieter hat Ihre E-Mail-Adresse nicht genannt …“** | Lassen Sie den Identitätsanbieter das Attribut `mail` oder `email` senden. |
