# Benutzer, Gruppen und Rollen {#users-groups-and-roles}

Jeder, der Fadenstack nutzt, meldet sich mit einem Konto an. Diese Seite zeigt, wie Sie Konten erstellen, was
Rollen und Gruppen den Benutzern geben und wie Sie jemandem den Zugang entziehen.

## Bevor Sie beginnen {#before-you-start}

- Sie sind als **Superuser** an der Konsole angemeldet. Die Seiten liegen unter **Zugriffskontrolle**:
  **Benutzer**, **Rollen** und **Gruppen**.

## Wofür die Konsole und die Rollen da sind {#what-the-console-and-the-roles-are-for}

- **Die Konsole ist für Superuser.** Wer kein Superuser ist, landet nach der Anmeldung im Chat, gleich welche
  Rollen er hat. Machen Sie jemanden nur dann zum Superuser, wenn er Fadenstack mit Ihnen einrichtet: Ein
  Superuser darf alles.
- **Rollen legen fest, was alle anderen dürfen**: im Chat, auf ihren eigenen Seiten **Fähigkeiten** und
  **Agenten** und über die API mit einem API-Token. Eine Rolle bündelt Berechtigungen. Die integrierten Rollen
  lassen sich weder ändern noch löschen.

| Rolle | Umfasst |
|---|---|
| `default_user` | Den Chat: Chats, Projekte und Anhänge, eigene Skills (erstellen und teilen) und das Lesen der Sammlungen der Wissensdatenbank, die für den Benutzer freigegeben sind. Jedes neue Konto erhält diese Rolle, sofern Sie keine anderen Rollen auswählen. |
| `viewer` | Das Lesen des Zustands von Modellen, Clustern, Maschinen, Containern und Logs über die API. |
| `operator` | Was `viewer` umfasst, und das Betreiben von Modellen über die API: Cluster und Bereitstellungen erstellen, ändern, skalieren, stoppen und starten. Nicht löschen. |
| `admin` | Jede Berechtigung, darunter Löschen, einen Skill für die ganze Organisation sichtbar machen (`skills:publish`), jeden Skill verwalten (`skills:manage`) sowie Agenten erstellen und löschen (`agents:create`, `agents:delete`). |
| `rag_manager`, `rag_editor`, `rag_viewer` | Das Verwalten oder Bearbeiten der Wissensdatenbank (`rag_manager`, `rag_editor`: Sie sehen jede Sammlung und legen fest, wer welche liest) oder das Lesen (`rag_viewer`). |

![Zugriffskontrolle → Rollen: die integrierten Rollen, jeweils mit der Zahl ihrer Berechtigungen und Benutzer, als „Schreibgeschützt“ markiert, und „Rolle erstellen“ oben](../images/administer/roles.png)

!!! note
    Rollen, die Sie ankreuzen, ersetzen `default_user`. Soll jemand auch chatten, kreuzen Sie zusätzlich
    `default_user` an.

## Wer welche Sammlung liest {#who-reads-which-collection}

Jede Sammlung der Wissensdatenbank steht entweder allen offen, die den Chat nutzen (**Alle, die den Chat
nutzen**), oder nur den Personen und Gruppen, die Sie auswählen (**Nur diese Personen und Gruppen**). Eine
Sammlung, die für jemanden nicht freigegeben ist, verbirgt auch alles darin vor ihm, selbst eine untergeordnete
Sammlung, die für alle freigegeben ist. Seine Suchen finden deren Dokumente nicht, und die Dokumente sind für ihn
nicht vorhanden.

1. Öffnen Sie **RAG → Wissensdatenbank** und zeigen Sie mit der Maus auf die Sammlung.
2. Wählen Sie **Wer darf es lesen** (das Schloss-Symbol).
3. Wählen Sie **Nur diese Personen und Gruppen**, fügen Sie Personen oder Gruppen hinzu und wählen Sie
   **Speichern**.

Eine eingeschränkte Sammlung zeigt ein Schloss neben ihrem Namen. Superuser und die Verwalter der
Wissensdatenbank (`admin`, `rag_manager`, `rag_editor`) sehen jede Sammlung. Ein Agent durchsucht nur die
Sammlungen, die in seiner Definition unter **Wissen** ausgewählt sind, und davon nur die, die sein Benutzer lesen
darf.

## Eine Person hinzufügen {#add-a-person}

1. Öffnen Sie **Zugriffskontrolle → Benutzer** und wählen Sie **Benutzer erstellen**.
2. Geben Sie die E-Mail-Adresse der Person und ein erstes Passwort mit mindestens 6 Zeichen ein.
3. Lassen Sie **Superuser** ausgeschaltet, es sei denn, die Person verwaltet Fadenstack mit Ihnen.
4. Lassen Sie unter **Rollen zuweisen** alles leer für Personen, die nur chatten: Sie erhalten `default_user`.
   Kreuzen Sie Rollen für alle an, die mehr brauchen, und zusätzlich `default_user`, wenn sie auch chatten.
5. Wählen Sie **Benutzer erstellen**.

![Neuen Benutzer erstellen: „E-Mail“ und „Passwort“, der Schalter „Superuser“ und „Rollen zuweisen“ mit den integrierten Rollen zum Ankreuzen](../images/administer/users-create.png)

Das Konto ist sofort aktiv. Geben Sie das erste Passwort persönlich oder telefonisch weiter, nicht in derselben
E-Mail wie die Adresse. Die Person meldet sich unter `https://ai.example.internal` an, landet im Chat und kann
das Passwort unter **Profil verwalten** ändern (siehe [Ihr Konto](../use/account.md)).

Es gibt keine Einladungen: Sie legen jedes Konto selbst an. Die Anmeldung mit dem Firmenkonto Ihrer Organisation
(Single Sign-on) ist <span class="fs-planned">Geplant</span>: Die Benutzer melden sich dann mit dem Konto an, das
sie bereits haben, und erhalten bei der ersten Anmeldung ein Fadenstack-Konto.

## Ändern, was jemand darf {#change-what-someone-may-do}

1. Öffnen Sie **Zugriffskontrolle → Benutzer** und wählen Sie in der Zeile der Person **Rollen bearbeiten**.
2. Kreuzen Sie Rollen an oder entfernen Sie die Häkchen.
3. Wählen Sie die **Nutzungsgruppe**: die Gruppe, der die Nutzung der Person zugerechnet wird (siehe
   [Gruppen](#groups)).
4. Wählen Sie **Speichern**.

Die Tabelle zeigt für jede Person die Flags (**Superuser**, **Aktiv**, **Verifiziert**), die Rollen, die
Nutzungsgruppe und wie viele Berechtigungen sie insgesamt hat. Das Suchfeld findet Personen nach E-Mail, Rolle
oder Berechtigung.

![Zugriffskontrolle → Benutzer: Flags, Rollen, Nutzungsgruppe und Zahl der Berechtigungen jeder Person, in jeder Zeile „Rollen bearbeiten“, das Code-Symbol und der Papierkorb, und „Benutzer erstellen“ oben](../images/administer/users.png)

## Eigene Rollen {#roles-of-your-own}

Wenn keine integrierte Rolle passt, erstellen Sie eine:

1. Öffnen Sie **Zugriffskontrolle → Rollen** und wählen Sie **Rolle erstellen**.
2. Geben Sie ihr einen Namen und eine Beschreibung.
3. Kreuzen Sie ihre **Berechtigungen** an, z. B. `agents:create` für Personen, die für ihr Team einen
   Browser-Agenten einrichten dürfen, oder `skills:publish` für Personen, die Skills mit der ganzen Organisation
   teilen dürfen.
4. Speichern Sie die Rolle und weisen Sie sie Personen oder einer Gruppe zu.

Wenn Sie eine Rolle löschen, verlieren alle sie, die sie haben; der Dialog nennt, wie viele Personen das sind.

## Gruppen {#groups}

Eine Gruppe fasst Personen zusammen, für drei Dinge:

- **Rollen.** Die Rollen einer Gruppe gelten für alle ihre Mitglieder, zusätzlich zu deren eigenen.
- **Teilen.** Skills und Agenten lassen sich mit einer Gruppe teilen statt mit jeder einzelnen Person (siehe
  [Skills](skills.md) und [Agenten](agents.md)).
- **Nutzung.** Die **Nutzungsgruppe** einer Person ist die Gruppe, der ihre Anfragen zugerechnet werden (siehe
  [Nutzung und Kosten](usage.md)). Sie wird pro Person festgelegt, auf der Seite **Benutzer**.

So erstellen Sie eine Gruppe:

1. Öffnen Sie **Zugriffskontrolle → Gruppen** und wählen Sie **Gruppe erstellen**.
2. Geben Sie ihr einen Namen und eine Beschreibung, und kreuzen Sie unter **Rollen zuweisen** ihre Rollen an.
3. Speichern Sie sie, und wählen Sie dann **Mitglieder verwalten** und **Mitglieder hinzufügen**.

Wenn Sie jemanden aus einer Gruppe entfernen, verliert die Person die Rollen der Gruppe. Mit der Gruppe geteilte
Skills erreichen ihre Chats innerhalb einer Minute nicht mehr.

## Entwicklermodus {#developer-mode}

Der **Entwicklermodus** (das Code-Symbol in der Zeile einer Person) erlaubt Agenten, jeden MCP-Server einzubinden,
der auf dem eigenen Computer dieser Person läuft. Er ist für Personen gedacht, die Tools entwickeln oder testen. Er
erweitert nie, was der Server erlaubt, erreicht die Agenten der Person, wenn ihre Anmeldung das nächste Mal
erneuert wird, und kann zu einem Zeitpunkt enden, den Sie unter **Endet** festlegen. Lassen Sie ihn für alle
anderen ausgeschaltet.

## Anmeldungen und API-Tokens {#sign-ins-and-api-tokens}

- **Eine Anmeldung gilt 7 Tage.** Danach melden sich die Benutzer erneut an. Sie können das unter
  **Einstellungen → Allgemein** ändern: **Sign-in lifetime (s)**.
- **API-Tokens** erlauben Skripten und Anwendungen, die Modelle über die API zu nutzen. Jede Person erstellt ihre
  eigenen unter **Profil verwalten** im Chat (oder **Mein Profil** in der Konsole), gibt ihnen einen Namen und
  eine Gültigkeitsdauer und kann sie dort widerrufen. Anwendungen, die ein widerrufenes Token verwenden,
  funktionieren innerhalb einer Minute nicht mehr.
- **Jedes API-Token läuft ab**: nach 90 Tagen, sofern die Person, die es erstellt, keine andere Gültigkeitsdauer
  wählt, und spätestens nach einem Jahr. Ändern Sie diese Werte unter **Einstellungen → Allgemein**:
  **API token lifetime (s)** und **Longest API token lifetime (s)**.
- **Ein Administrator kann das Token einer anderen Person widerrufen**: Wählen Sie unter
  **Zugriffskontrolle → Benutzer** in deren Zeile **API-Tokens** (das Schlüssel-Symbol) und dann **Widerrufen**.
- **Agenten** (der Browser-Agent, die Office-Add-ins) haben auf jedem Gerät eigene Anmeldungen. Auf dem Tab
  **Aktivität** eines Agenten sehen Sie die Geräte und können sie abmelden (siehe
  [Agenten und Add-ins](agents.md)).

## Jemandem den Zugang entziehen {#end-someones-access}

So hindern Sie jemanden daran, Fadenstack zu nutzen, vorübergehend oder dauerhaft:

1. Öffnen Sie **Zugriffskontrolle → Benutzer** und wählen Sie in der Zeile der Person **Sperren** (das
   Personen-Symbol mit einem Strich hindurch).
2. Bestätigen Sie.

Die Person kann sich nicht mehr anmelden, und ihre Anmeldungen, API-Tokens und Agenten funktionieren innerhalb
einer Minute nicht mehr. Nichts von ihr wird gelöscht: **Wieder freigeben** an derselben Stelle gibt ihr den
Zugang zurück. Sie können weder sich selbst sperren noch den letzten Superuser.

**Löschen** (das Papierkorb-Symbol) entfernt das Konto und lässt sich nicht rückgängig machen.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**„User with email '…' already exists“.** Es gibt bereits ein Konto für diese Adresse. Suchen Sie es stattdessen
in der Liste.

**Jemand landet im Chat, wenn er die Konsole öffnet.** Die Person ist kein Superuser. Rollen öffnen die Konsole
nicht.

**Jemand kann nicht chatten, nachdem Sie ihm eine Rolle gegeben haben.** Die angekreuzten Rollen haben
`default_user` ersetzt. Bearbeiten Sie die Rollen der Person und kreuzen Sie zusätzlich `default_user` an.

**Jemand hat sein Passwort vergessen.** Die Konsole hat keine Schaltfläche, um das Passwort einer anderen Person
zu setzen. Wenn der Server keine E-Mails zum Zurücksetzen versendet, setzt die Person, die ihn betreibt, auf dem
Server ein neues, z. B. mit `faden admin reset-password --email alex@example.com` (ohne Angabe wird ein Passwort
erzeugt). Geben Sie es weiter, und lassen Sie die Person es unter **Profil verwalten** ändern.
