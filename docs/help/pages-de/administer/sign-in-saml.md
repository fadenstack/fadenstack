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
2. Geben Sie den **Namen** ein und prüfen Sie den **Schlüssel** und die **Serveradresse**. Der Dialog zeigt
   nun drei Werte:

    | In der Konsole | Beim Identitätsanbieter |
    |---|---|
    | **Metadaten-Adresse** | Die Metadaten des Dienstanbieters. Die meisten Identitätsanbieter lesen alles daraus. |
    | **Entity-ID** | Die Kennung (Entity-ID) des Dienstanbieters. Leer: die Metadaten-Adresse. Geben Sie nur dann eine andere an, wenn der Identitätsanbieter diesen Server schon darunter kennt. |
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
| **Vom Metadatendienst einer Föderation (MDQ)** | Statt einer Adresse oder eingefügtem XML: der Metadaten-Abfragedienst der Föderation und die **Entity-ID** des Identitätsanbieters. Fadenstack fragt den Dienst nach genau diesem Anbieter. |
| **Zertifikat, mit dem die Metadaten signiert sind** | Wenn Ihre Föderation ihre Metadaten signiert: ihr Signaturzertifikat (PEM). Dann werden nur Metadaten angenommen, die mit dem Schlüssel dieses Zertifikats signiert sind und ein `validUntil`-Datum haben. Pflicht mit einem Metadatendienst. |
| **Auch beim Identitätsanbieter abmelden** | Die Abmeldung bei Fadenstack meldet die Person auch beim Identitätsanbieter ab, wenn seine Metadaten einen Single-Logout-Dienst nennen. |
| **Anmeldungen beenden, wenn der Identitätsanbieter jemanden abmeldet** | Meldet der Identitätsanbieter eine Person ab, enden alle ihre Fadenstack-Anmeldungen, in jedem Browser und jeder Anwendung. Gefunden werden nur Anmeldungen, die entstanden, während dies eingeschaltet war. |

Ist einer der beiden Abmelde-Schalter an, tragen Sie die Adresse des **Single-Logout-Dienstes (SLO)**, die der
Dialog zeigt, beim Identitätsanbieter ein, wie die ACS. Abmeldenachrichten müssen vom Identitätsanbieter signiert
sein; unsignierte oder veränderte werden abgelehnt.

Metadaten werden bis 2 MiB gelesen, eingefügt oder abgerufen (`FADEN_AUTH_SAML_METADATA_MAX_BYTES` im
Anmeldemodul). Metadaten nach ihrem `validUntil`-Datum werden abgelehnt. Abgerufene Metadaten werden erneut
abgerufen, wenn ihre `cacheDuration` endet, spätestens nach einer Stunde, oder wenn sie ablaufen; solange sie nicht
abgerufen werden können, wird die Anmeldung mit diesem Anbieter abgelehnt. Eine Änderung an einem Anbieter gilt ab
der nächsten Anmeldung, in jedem Prozess des Anmeldemoduls.

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

??? info "Okta"
    1. Wählen Sie in der Okta Admin Console **Applications → Create App Integration**, dann **SAML 2.0**, und
       benennen Sie die Anwendung.
    2. Tragen Sie unter **Configure SAML** die **ACS**-Adresse als **Single sign-on URL** und die **Entity-ID**
       als **Audience URI (SP Entity ID)** ein.
    3. Fügen Sie unter **Attribute Statements** zwei hinzu:
        - Name `urn:oasis:names:tc:SAML:attribute:subject-id`, Namensformat **URI Reference**, Wert
          `user.getInternalProperty("id")`: die ID der Person bei Okta, dieselbe, die seine Provisionierung
          sendet. Die NameID von Okta ist die E-Mail-Adresse, die sich ändern kann, also keine ID.
        - Name `email`, Wert `user.email`.

        In neueren Konsolen stehen sie auf dem Reiter **Sign On** unter **Attribute Statements → Show legacy
        configuration**.
    4. Wählen Sie in der Konsole **Von seiner Adresse** und fügen Sie die **Metadata URL** vom Reiter **Sign On**
       bei Okta ein.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was zu tun ist |
|---|---|
| **„Der Anmeldedienst ist gerade nicht erreichbar.“** | Das Anmeldemodul konnte die Metadaten des Identitätsanbieters nicht von seiner Adresse lesen. Prüfen Sie, ob der Server sie über HTTPS erreicht, oder fügen Sie stattdessen das XML ein. |
| Der Identitätsanbieter meldet, Antwort-URL oder Entity seien unbekannt | Tragen Sie **Entity-ID** und **ACS**-Adresse genau so ein, wie der Dialog sie zeigt. |
| **„Die Anmeldung hat nicht geklappt.“** nach dem Identitätsanbieter | Die Antwort war unsigniert, für einen anderen Dienstanbieter, zu alt, oder nannte die Person mit nichts, das gleich bleibt. **Protokolle → Audit** sagt, was davon: `auth.sign_in.refused` für den Anbieter, mit dem Grund (`no_stable_subject`: Senden Sie eine persistente NameID oder `subject-id`, siehe Okta oben). Prüfen Sie die Einstellungen des Identitätsanbieters für diesen Dienstanbieter. |
| **„Der Anbieter hat Ihre E-Mail-Adresse nicht genannt …“** | Lassen Sie den Identitätsanbieter das Attribut `mail` oder `email` senden. |
| Die Abmeldung lässt Personen beim Identitätsanbieter angemeldet | Schalten Sie **Auch beim Identitätsanbieter abmelden** ein; es gilt für Anmeldungen danach. Das Protokoll des Anmeldedienstes sagt, warum es nicht weiterging (`no_slo_endpoint`: Die Metadaten des Anbieters nennen keinen Abmeldedienst). |
| Die Abmeldung beim Identitätsanbieter beendet die Anmeldungen hier nicht | Schalten Sie **Anmeldungen beenden, wenn der Identitätsanbieter jemanden abmeldet** ein und tragen Sie dort die Adresse des **Single-Logout-Dienstes (SLO)** ein. **Protokolle → Audit** zeigt `auth.sign_out.provider` für jede Abmeldung und `auth.sign_out.refused` mit dem Grund für eine abgelehnte Nachricht (`signature`, `destination`, `stale`, ...). |
