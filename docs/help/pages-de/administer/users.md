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
    | `admin` | Jede Berechtigung, darunter Löschen, einen Skill für die ganze Organisation sichtbar machen (`skills:publish`), jeden Skill verwalten (`skills:manage`) sowie Agenten erstellen und löschen (`agents:create`, `agents:delete`). |
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
2. Geben Sie die E-Mail-Adresse der Person und ein erstes Passwort mit mindestens 6 Zeichen ein.
3. Lassen Sie **Superuser** ausgeschaltet, es sei denn, die Person verwaltet Fadenstack mit Ihnen.
4. Lassen Sie unter **Rollen zuweisen** alles leer für Personen, die nur chatten: Sie erhalten `default_user`.
5. Wählen Sie **Benutzer erstellen**.

Das Konto ist sofort aktiv. Geben Sie das erste Passwort persönlich oder telefonisch weiter; die Person meldet
sich unter `https://ai.example.internal` an, landet im Chat und kann es unter **Profil verwalten** ändern.

??? info "Einladungen und Single Sign-on"
    Es gibt keine Einladungen: Sie legen jedes Konto selbst an. Die Anmeldung mit dem Firmenkonto Ihrer
    Organisation (Single Sign-on) ist <span class="fs-planned">Geplant</span>: Die Benutzer melden sich dann mit dem
    Konto an, das sie bereits haben, und erhalten bei der ersten Anmeldung ein Fadenstack-Konto. Siehe auch
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
  [Nutzung und Kosten](usage.md)). Sie wird pro Person festgelegt, auf der Seite **Benutzer**.

So erstellen Sie eine: Öffnen Sie **Zugriffskontrolle → Gruppen**, wählen Sie **Gruppe erstellen**, geben Sie ihr
einen Namen, kreuzen Sie unter **Rollen zuweisen** ihre Rollen an, speichern Sie sie, und wählen Sie dann
**Mitglieder verwalten** und **Mitglieder hinzufügen**.

Wenn Sie jemanden aus einer Gruppe entfernen, verliert die Person die Rollen der Gruppe. Mit der Gruppe geteilte
Skills erreichen ihre Chats innerhalb einer Minute nicht mehr.

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
| **Eine Anmeldung** | gilt 7 Tage; danach melden sich die Benutzer erneut an. |
| **API-Tokens** | erlauben Skripten und Anwendungen, die Modelle über die API zu nutzen. Jede Person erstellt ihre eigenen unter **Profil verwalten** (oder **Mein Profil** in der Konsole) und kann sie dort widerrufen. |
| **Jedes API-Token läuft ab** | nach 90 Tagen, sofern die Person, die es erstellt, keine andere Gültigkeitsdauer wählt, und spätestens nach einem Jahr. |
| **Agenten** (der Browser-Agent, die Office-Add-ins) | haben auf jedem Gerät eigene Anmeldungen, zu sehen auf dem Tab **Aktivität** eines Agenten (siehe [Agenten und Add-ins](agents.md)). |

![Die API-Tokens einer Person: jedes Token mit Namen, Erstellungsdatum und Ablaufdatum](../images/administer/user-api-tokens.png)

Das Token einer anderen Person widerrufen: Wählen Sie unter **Zugriffskontrolle → Benutzer** in deren Zeile
**API-Tokens** (das Schlüssel-Symbol) und dann **Widerrufen**. Anwendungen, die ein widerrufenes Token verwenden,
funktionieren innerhalb einer Minute nicht mehr.

![Einstellungen → Allgemein: „Sign-in lifetime (s)“, „API token lifetime (s)“ und „Longest API token lifetime (s)“](../images/administer/settings-sign-in.png)

Die Gültigkeitsdauern ändern Sie unter **Einstellungen → Allgemein**: **Sign-in lifetime (s)**, **API token
lifetime (s)** und **Longest API token lifetime (s)**.

## Jemandem den Zugang entziehen {#end-someones-access}

![Konto sperren: Die Person kann sich nicht mehr anmelden, nichts von ihr wird gelöscht, und Sie können sie wieder freigeben](../images/administer/user-suspend.png)

1. Öffnen Sie **Zugriffskontrolle → Benutzer** und wählen Sie in der Zeile der Person **Sperren** (das
   Personen-Symbol mit einem Strich hindurch).
2. Bestätigen Sie.

Ihre Anmeldungen, API-Tokens und Agenten funktionieren innerhalb einer Minute nicht mehr. Nichts von ihr wird
gelöscht: **Wieder freigeben** an derselben Stelle gibt ihr den Zugang zurück. Sie können weder sich selbst
sperren noch den letzten Superuser.

**Löschen** (das Papierkorb-Symbol) entfernt das Konto und lässt sich nicht rückgängig machen.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **„User with email '…' already exists“** | Es gibt bereits ein Konto für diese Adresse. Suchen Sie es stattdessen in der Liste. |
| Jemand landet im Chat, wenn er die Konsole öffnet | Die Person ist kein Superuser. Rollen öffnen die Konsole nicht. |
| Jemand kann nicht chatten, nachdem Sie ihm eine Rolle gegeben haben | Die angekreuzten Rollen haben `default_user` ersetzt. Bearbeiten Sie die Rollen der Person und kreuzen Sie zusätzlich `default_user` an. |
| Jemand hat sein Passwort vergessen | Die Konsole kann das Passwort einer anderen Person nicht setzen. Wenn der Server keine E-Mails zum Zurücksetzen versendet, setzt die Person, die ihn betreibt, ein neues, z. B. mit `faden admin reset-password --email alex@example.com` (ohne Angabe wird eines erzeugt). Lassen Sie die Person es unter **Profil verwalten** ändern. |
