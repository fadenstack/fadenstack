# Benutzer, Gruppen und Rollen {#users-groups-and-roles}

Jeder, der Fadenstack nutzt, meldet sich mit einem Konto an. Legen Sie Konten an, geben Sie Benutzern Rollen und
Gruppen, und entziehen Sie jemandem den Zugang.

![Zugriffskontrolle → Benutzer: Flags, Rollen, Nutzungsgruppe und Zahl der Berechtigungen jeder Person, und „Benutzer erstellen“ oben](../images/administer/users.png)

## Bevor Sie beginnen {#before-you-start}

- Sie sind als **Superuser** an der Konsole angemeldet. Die Seiten liegen unter **Zugriffskontrolle**:
  **Benutzer**, **Rollen** und **Gruppen**.

## Wofür die Konsole und die Rollen da sind {#what-the-console-and-the-roles-are-for}

- **Die Konsole ist für Superuser.** Alle anderen landen nach der Anmeldung im Chat, gleich welche Rollen sie
  haben. Ein Superuser darf alles: Machen Sie jemanden nur dann dazu, wenn er Fadenstack mit Ihnen einrichtet.
- **Rollen legen fest, was alle anderen dürfen**: im Chat, auf ihren eigenen Seiten **Skills** und **Agenten**
  und über die API. Eine Rolle bündelt Berechtigungen; die integrierten Rollen lassen sich weder ändern noch
  löschen.

![Zugriffskontrolle → Rollen: die integrierten Rollen, jeweils mit der Zahl ihrer Berechtigungen und Benutzer, als „Schreibgeschützt“ markiert, und „Rolle erstellen“ oben](../images/administer/roles.png)

??? info "Was jede integrierte Rolle umfasst"
    | Rolle | Umfasst |
    |---|---|
    | `default_user` | Den Chat: Chats, Projekte und Anhänge, im Chat und über die API, eigene Skills (erstellen und teilen) und das Lesen der Sammlungen der Wissensdatenbank, die für den Benutzer freigegeben sind. Jedes neue Konto erhält diese Rolle, sofern Sie keine anderen Rollen auswählen. |
    | `viewer` | Das Lesen des Zustands von Modellen, Clustern, Maschinen, Containern und Logs über die API. |
    | `operator` | Was `viewer` umfasst, und das Betreiben von Modellen über die API: Cluster und Bereitstellungen erstellen, ändern, skalieren, stoppen und starten. Nicht löschen. |
    | `admin` | Jede Berechtigung bis auf eine, darunter Löschen, einen Skill für die ganze Organisation sichtbar machen (`skills:publish`), jeden Skill verwalten (`skills:manage`), Agenten erstellen und löschen (`agents:create`, `agents:delete`) sowie die Nutzungszahlen für Gruppen (`usage:read`). Nicht die, die `usage_auditor` gibt. |
    | `usage_auditor` | Die Nutzungszahlen einer einzelnen Person ansehen, solange die personenbezogene Auswertung eingeschaltet ist, jedes Mal mit Begründung (`usage.people:audit`). Bei der Installation hat sie niemand, und ein Superuser hat sie nicht von selbst. Siehe [Nutzung und Datenschutz](usage-privacy.md#looking-at-one-persons-figures). |
    | `rag_manager`, `rag_editor`, `rag_viewer` | Das Verwalten oder Bearbeiten der Wissensdatenbank (`rag_manager`, `rag_editor`: Sie sehen jede Sammlung und legen fest, wer welche liest) oder das Lesen (`rag_viewer`). |

!!! note
    Rollen, die Sie ankreuzen, ersetzen `default_user`. Soll jemand auch chatten, kreuzen Sie zusätzlich
    `default_user` an: Ohne diese Rolle oder eine eigene Rolle mit den Berechtigungen `chat.*` chattet niemand,
    weder im Chat noch über die API. Superuser brauchen keine Rolle.

## Wer welche Sammlung liest {#who-reads-which-collection}

Jede Sammlung der Wissensdatenbank steht entweder allen offen, die den Chat nutzen (**Alle, die den Chat
nutzen**), oder nur den Personen und Gruppen, die Sie auswählen.

1. Öffnen Sie **RAG → Wissensdatenbank** und zeigen Sie mit der Maus auf die Sammlung.
2. Wählen Sie **Wer darf es lesen** (das Schloss-Symbol).
3. Wählen Sie **Nur diese Personen und Gruppen**, fügen Sie Personen oder Gruppen hinzu und wählen Sie
   **Speichern**.

Eine eingeschränkte Sammlung zeigt ein Schloss neben ihrem Namen.

??? info "Mehr dazu, wer eine Sammlung liest"
    Eine Sammlung, die für jemanden nicht freigegeben ist, verbirgt auch alles darin vor ihm, selbst eine
    untergeordnete Sammlung, die für alle freigegeben ist: Seine Suchen finden deren Dokumente nicht. Superuser und
    die Verwalter der Wissensdatenbank (`admin`, `rag_manager`, `rag_editor`) sehen jede Sammlung. Ein Agent
    durchsucht nur die Sammlungen, die in seiner Definition unter **Wissen** ausgewählt sind, und davon nur die, die
    sein Benutzer lesen darf.

## Eine Person hinzufügen {#add-a-person}

![Neuen Benutzer erstellen: „E-Mail“ und „Passwort“, der Schalter „Superuser“ und „Rollen zuweisen“ mit den integrierten Rollen zum Ankreuzen](../images/administer/users-create.png)

1. Öffnen Sie **Zugriffskontrolle → Benutzer** und wählen Sie **Benutzer erstellen**.
2. Geben Sie die E-Mail-Adresse der Person und ein erstes Passwort mit mindestens 8 Zeichen ein.
3. Lassen Sie **Superuser** ausgeschaltet, es sei denn, die Person verwaltet Fadenstack mit Ihnen.
4. Lassen Sie unter **Rollen zuweisen** alles leer für Personen, die nur chatten: Sie erhalten `default_user`.
5. Wählen Sie **Benutzer erstellen**.

Das Konto ist sofort aktiv. Geben Sie das erste Passwort persönlich oder telefonisch weiter; die Person meldet
sich unter `https://ai.example.internal` an, landet im Chat und kann es unter **Profil verwalten** ändern.

??? info "Einladungen und Single Sign-on"
    Es gibt keine Einladungen: Sie legen jedes Konto selbst an, oder die Benutzer erhalten eines, wenn sie sich
    zum ersten Mal mit dem Konto Ihrer Organisation anmelden (siehe [Anmeldeanbieter](sign-in.md)). Siehe auch
    [Ihr Konto](../use/account.md).

## Ändern, was jemand darf {#change-what-someone-may-do}

![„Rollen bearbeiten“ für eine Person: die Rollen zum Ankreuzen und ihre Nutzungsgruppe](../images/administer/users-edit-roles.png)

1. Öffnen Sie **Zugriffskontrolle → Benutzer** und wählen Sie in der Zeile der Person **Rollen bearbeiten**.
2. Kreuzen Sie Rollen an oder entfernen Sie Häkchen, und wählen Sie die **Nutzungsgruppe**: die Gruppe, der die
   Nutzung der Person zugerechnet wird (siehe [Gruppen](#groups)).
3. Wählen Sie **Speichern**.

Das Suchfeld über der Tabelle findet Personen nach E-Mail, Rolle oder Berechtigung.

## Eigene Rollen {#roles-of-your-own}

Wenn keine integrierte Rolle passt, erstellen Sie eine:

1. Öffnen Sie **Zugriffskontrolle → Rollen** und wählen Sie **Rolle erstellen**.
2. Geben Sie ihr einen Namen und eine Beschreibung, und kreuzen Sie ihre **Berechtigungen** an, z. B.
   `agents:create` für Personen, die für ihr Team einen Browser-Agenten einrichten dürfen, oder `skills:publish`
   für Personen, die Skills mit der ganzen Organisation teilen dürfen.
3. Speichern Sie die Rolle und weisen Sie sie Personen oder einer Gruppe zu.

Wenn Sie eine Rolle löschen, verlieren alle sie, die sie haben; der Dialog nennt, wie viele Personen das sind.

## Gruppen {#groups}

![Gruppe erstellen: Name, Beschreibung und die Rollen, die die Gruppe ihren Mitgliedern gibt](../images/administer/groups.png)

Eine Gruppe fasst Personen zusammen, für drei Dinge:

- **Rollen.** Die Rollen einer Gruppe gelten für alle ihre Mitglieder, zusätzlich zu deren eigenen.
- **Teilen.** Skills und Agenten lassen sich mit einer Gruppe teilen (siehe [Skills](skills.md) und
  [Agenten](agents.md)).
- **Nutzung.** Die **Nutzungsgruppe** einer Person ist die Gruppe, der ihre Anfragen zugerechnet werden (siehe
  [Nutzung und Limits](usage.md)). Sie wird pro Person festgelegt, auf der Seite **Benutzer**.

So erstellen Sie eine: Öffnen Sie **Zugriffskontrolle → Gruppen**, wählen Sie **Gruppe erstellen**, geben Sie ihr
einen Namen, kreuzen Sie unter **Rollen zuweisen** ihre Rollen an, speichern Sie sie, und wählen Sie dann
**Mitglieder verwalten** und **Mitglieder hinzufügen**.

Wenn Sie jemanden aus einer Gruppe entfernen, verliert die Person die Rollen der Gruppe, und mit der Gruppe
geteilte Skills erreichen ihre Chats sofort nicht mehr. Der Eintritt in eine Gruppe oder der Austritt meldet die
Person ab, wie jede Änderung ihrer Rechte (siehe [Anmeldungen und API-Tokens](#sign-ins-and-api-tokens)).

## Entwicklermodus {#developer-mode}

![Entwicklermodus für eine Person: der Schalter und „Endet“, um festzulegen, wann er aufhört](../images/administer/users-developer-mode.png)

Der **Entwicklermodus** (das Code-Symbol in der Zeile einer Person) erlaubt Agenten, jeden MCP-Server einzubinden,
der auf dem eigenen Computer dieser Person läuft. Er ist für Personen gedacht, die Tools entwickeln oder testen, und
kann zu einem Zeitpunkt enden, den Sie unter **Endet** festlegen. Lassen Sie ihn für alle anderen ausgeschaltet.

??? info "Was der Entwicklermodus nicht tut"
    Er erweitert nie, was der Server erlaubt. Er erreicht die Agenten der Person, wenn ihre Anmeldung das nächste
    Mal erneuert wird.

## Anmeldungen und API-Tokens {#sign-ins-and-api-tokens}

| | |
|---|---|
| **Eine Anmeldung** | gilt 7 Tage; danach melden sich die Benutzer erneut an. **Abmelden** beendet sie sofort. Eine Passwortänderung oder ein Zurücksetzen, eine Sperre und jede Änderung der Rechte einer Person beenden sofort alle ihre Anmeldungen, auch die der Agenten auf ihren Geräten: eine Rolle gegeben oder entzogen, der Eintritt in eine Gruppe oder der Austritt, eine Änderung an den Rollen ihrer Gruppen oder an den Berechtigungen ihrer Rollen, das Administratorrecht gegeben oder entzogen. Sie meldet sich erneut an. Jede wird im Audit-Protokoll festgehalten (`user.sign_ins.ended`, mit dem Grund). |
| **API-Tokens** | erlauben Skripten und Anwendungen, die Modelle über die API zu nutzen. Jede Person erstellt ihre eigenen unter **Profil verwalten** (oder **Mein Profil** in der Konsole) und kann sie dort widerrufen. |
| **Jedes API-Token läuft ab** | nach 90 Tagen, sofern die Person, die es erstellt, keine andere Gültigkeitsdauer wählt, und spätestens nach einem Jahr. |
| **Agenten** (der Browser-Agent, die Office-Add-ins) | haben auf jedem Gerät eigene Anmeldungen, zu sehen auf dem Tab **Aktivität** eines Agenten (siehe [Agenten und Add-ins](agents.md)). |

![Die API-Tokens einer Person: jedes Token mit Namen, Erstellungsdatum, Ablaufdatum und letzter Nutzung](../images/administer/user-api-tokens.png)

Das Token einer anderen Person widerrufen: Wählen Sie unter **Zugriffskontrolle → Benutzer** in deren Zeile
**API-Tokens** (das Schlüssel-Symbol) und dann **Widerrufen**. Anwendungen, die ein widerrufenes Token verwenden,
funktionieren sofort nicht mehr.

Jede Anfrage mit einem API-Token wird festgehalten: wann, von welcher Adresse, mit welcher Anwendung und auf
welchem Weg; nie, was gesendet wurde. **Zugriffskontrolle → API-Tokens** listet die Tokens aller Personen mit
Inhaber und letzter Nutzung; **Nutzungen** zeigt die letzten 100 Nutzungen eines Tokens. Jede Person sieht
dasselbe für ihre eigenen Tokens unter **Profil verwalten**. Die Nutzung eines Tokens zählt auch als Aktivität:
Die Regel eines Anbieters, nach Tagen ohne Nutzung zu sperren, sperrt so niemanden, der nur über Tokens arbeitet.
Die Nutzungen werden 90 Tage aufbewahrt und dann gelöscht (`FADEN_BACKEND_API_TOKEN_USE_RETENTION_DAYS` auf dem
Server; 0 behält sie).

![Zugriffskontrolle → API-Tokens: jedes aktive Token mit Inhaber, Erstellungs- und Ablaufdatum, letzter Nutzung (Zeit, Adresse, Anwendung, Nutzungen in 30 Tagen), Nutzungen und Widerrufen](../images/administer/api-tokens.png)

![Die Nutzungen eines Tokens: wann, von welcher Adresse, mit welcher Anwendung, auf welchem Weg](../images/administer/api-token-uses.png)

![Einstellungen → Allgemein: „Sign-in lifetime (s)“, „API token lifetime (s)“ und „Longest API token lifetime (s)“](../images/administer/settings-sign-in.png)

Die Gültigkeitsdauern ändern Sie unter **Einstellungen → Allgemein**: **Sign-in lifetime (s)**, **API token
lifetime (s)** und **Longest API token lifetime (s)**.

### Verknüpfte Anmeldungen {#linked-sign-ins}

![Verknüpfte Anmeldungen einer Person: Example Corp, wann sie sich zuletzt damit angemeldet hat, und dass ihr Passwort sie nicht anmeldet](../images/administer/user-sign-ins.png)

Unter **Zugriffskontrolle → Benutzer** zeigt **Verknüpfte Anmeldungen** (das Ketten-Symbol) in der Zeile einer
Person die Anmeldeanbieter, mit denen ihr Konto verknüpft ist, und ob auch ihr Passwort sie anmeldet.
**Trennen** beendet eine Verknüpfung: Die Person kann sich damit nicht mehr anmelden, bis sie sie unter **Mein
Profil** wieder verknüpft. Das wird mit Ihrem Namen im Audit-Protokoll festgehalten.

Die letzte Art, wie sich eine Person anmeldet, lässt sich nicht trennen, damit niemand versehentlich ausgesperrt
wird. Soll jemand vom Anbieter zum Passwort wechseln, geben Sie ihm zuerst einen
[Link zum Zurücksetzen des Passworts](#a-forgotten-password). Um den Zugang zu beenden, sperren Sie stattdessen
das Konto (siehe [Jemandem den Zugang entziehen](#end-someones-access)).

## Ein vergessenes Passwort {#a-forgotten-password}

Wenn Ihr Server keine E-Mails versendet oder die Person sie nicht erhält, geben Sie ihr einen **Link zum
Zurücksetzen des Passworts**:

1. Starten Sie den **Root-Modus**.
2. Wählen Sie unter **Zugriffskontrolle → Benutzer** in ihrer Zeile **Link zum Zurücksetzen des Passworts**
   (das Schloss-Symbol).
3. Wählen Sie, wie lange er **Gültig für** ist (**1 Stunde**, oder **24 Stunden** für jemanden, den Sie später
   erreichen), und geben Sie eine **Begründung** an (mindestens 10 Zeichen).
4. Wählen Sie **Link erstellen**, dann **Link kopieren**, und geben Sie ihn der Person selbst, über einen Weg,
   den nur sie liest: persönlich, am Telefon, in einem Chat, dem Sie vertrauen.

Die Person öffnet ihn, sieht ihre Adresse und wählt ein neues Passwort; danach meldet sie sich damit an. Alle
ihre anderen Anmeldungen enden.

- Wer den Link hat, kann das Konto übernehmen. Deshalb braucht er den Root-Modus, und seine Begründung wird mit
  Ihrem Namen im Audit-Protokoll festgehalten, ebenso seine Verwendung.
- Er gilt einmal. Ein neuerer Link ersetzt ihn, ebenso eine Passwortänderung oder eine Sperre.
- Er wird nur beim Erstellen angezeigt; geht er verloren, erstellen Sie einen neuen.
- Nicht für Ihr eigenes Konto (ändern Sie es unter **Profil verwalten**) und nicht für ein gesperrtes.
- Er gibt auch einem Konto, das bei der Anmeldung über einen Anbieter entstanden ist, ein Passwort; ein solches
  Konto hat keines, das jemand kennt.

Auf dem Server setzt `faden admin reset-password --email alex@example.com` direkt ein neues Passwort (etwa
wenn sich kein Administrator anmelden kann).

## Jemandem den Zugang entziehen {#end-someones-access}

![Konto sperren: Die Person kann sich nicht mehr anmelden, nichts von ihr wird gelöscht, und Sie können sie wieder freigeben](../images/administer/user-suspend.png)

1. Öffnen Sie **Zugriffskontrolle → Benutzer** und wählen Sie in der Zeile der Person **Sperren** (das
   Personen-Symbol mit einem Strich hindurch).
2. Bestätigen Sie.

Ihre Anmeldungen, API-Tokens und Agenten funktionieren sofort nicht mehr. Nichts von ihr wird
gelöscht: **Wieder freigeben** an derselben Stelle gibt ihr den Zugang zurück, die bisherigen Anmeldungen bleiben
aber beendet: Sie meldet sich neu an. Sie können weder sich selbst sperren noch den letzten Superuser.

**Löschen** (das Papierkorb-Symbol) entfernt das Konto und lässt sich nicht rückgängig machen: Seine Chats,
Projekte, angehängten Dateien und das, was sich der Assistent dafür gemerkt hat, werden mit ihm gelöscht. Sie
können weder sich selbst löschen noch den letzten aktiven Superuser.

Wie lange Chats, die Personen selbst löschen, aufbewahrt werden, bevor sie endgültig gelöscht werden, steht unter
**Einstellungen → Chat** (30 Tage, wenn `faden deploy` nichts anderes gesagt wurde). Chat-Daten von Konten, die
vor dieser Version gelöscht wurden, sind auf dem Server geblieben; `faden chats orphans` listet sie auf und
`faden chats orphans --erase` löscht sie.

## Administratoren und Prüfer {#administrators-and-auditors}

Zwei Rechte betreffen Vertrauen statt Arbeit; jedes lässt sich vergeben, entziehen und übergeben:

- Ein **Administrator** (Superuser) darf in der Konsole alles.
- Ein **Prüfer** (die eingebaute Rolle `auditor`) liest das Audit-Protokoll und die Nutzungszahlen für Gruppen
  (**Observability → Nutzung**) und ändert nichts. Administratoren lesen sie auch; Betrachter und Operatoren
  nicht, weil das Audit-Protokoll E-Mail- und IP-Adressen enthält.

Starten Sie dafür zuerst den **Root-Modus**, öffnen Sie **Zugriffskontrolle → Benutzer** und wählen Sie in der
Zeile der Person **Rechte** (das Schild-Symbol):

- **Zum Administrator machen** oder **Administratorrecht entziehen** für diese Person.
- **Übergeben**: Das Recht (Administrator oder Prüfer) geht in einem Schritt von dieser Person an die Person,
  die Sie wählen. Wer es hatte, verliert es und wird abgemeldet.

Jede Änderung braucht eine Begründung von mindestens 10 Zeichen. Sie wird mit beiden Namen und der Begründung im
Audit-Protokoll festgehalten. Der letzte Administrator behält das Recht.

Kann sich niemand mehr als Administrator anmelden, macht jemand mit Zugang zum Server ein Konto wieder zum
Administrator (auch das wird festgehalten):

```bash
faden admin grant-admin --email person@example.com --reason "Der einzige Administrator hat das Unternehmen verlassen"
```

## Das Audit-Protokoll {#the-audit-log}

**Protokolle → Audit** (für einen Prüfer nur **Audit**) zeigt, wer was getan hat: jede Änderung in den
Verwaltungsbereichen, abgelehnte Versuche eingeschlossen, Anmeldungen und fehlgeschlagene Anmeldungen,
Passwortänderungen und -zurücksetzungen, vergebene und übergebene Rechte sowie das Lesen fremder Dinge oder
geheimer Werte: die Chat-Übersicht, die Observability-Seiten, die Protokollansicht und das Audit-Protokoll
selbst. Jeder Eintrag nennt die E-Mail-Adresse der Person, wie sie damals lautete, woher die Anfrage kam (die
Adresse und ob Konsole, API, CLI oder das System) und die Zeit.

- **Filter**: Person, Aktion (ein Name oder ein Präfix mit Punkt am Ende, etwa `user.`), Ergebnis, Schweregrad,
  Quelle und ein Zeitraum.
- **Als CSV exportieren** lädt herunter, was die Filter zeigen (höchstens 100.000 Einträge); der Export wird
  festgehalten.
- Die Zeile über der Tabelle sagt, ob das Protokoll **unverändert** ist: Jeder Eintrag trägt den Hash des
  vorherigen, sodass ein nachträglich geänderter, eingefügter oder gelöschter Eintrag die Kette an dieser Stelle
  bricht. Eine Kopie jedes Eintrags liegt außerdem in einer eigenen Datei, die jede Sicherung mitnimmt, und in
  den Protokollen des Servers.

Vom Server aus dieselbe Prüfung:

```bash
faden audit verify
```

Auch die eigenen Änderungen der CLI werden festgehalten: Sicherungen, Wiederherstellungen, Schlüsselrotationen,
Upgrades und Passwortzurücksetzungen mit `faden admin reset-password`. Fadenstack löscht das Audit-Protokoll
nie; es übersteht Upgrades, Sicherungen und Wiederherstellungen.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **„User with email '…' already exists“** | Es gibt bereits ein Konto für diese Adresse. Suchen Sie es stattdessen in der Liste. |
| Jemand landet im Chat, wenn er die Konsole öffnet | Die Person ist kein Superuser. Rollen öffnen die Konsole nicht. |
| Jemand kann nicht chatten, nachdem Sie ihm eine Rolle gegeben haben | Die angekreuzten Rollen haben `default_user` ersetzt. Bearbeiten Sie die Rollen der Person und kreuzen Sie zusätzlich `default_user` an. |
| Jemand hat sein Passwort vergessen | Ohne E-Mails zum Zurücksetzen geben Sie der Person einen Link zum Zurücksetzen des Passworts: siehe [Ein vergessenes Passwort](#a-forgotten-password). |
| **„Start Root Mode first: a password reset link is recorded with its reason.“** | Starten Sie den **Root-Modus** und erstellen Sie den Link erneut. |
