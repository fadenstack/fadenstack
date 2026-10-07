# Skills für die Organisation {#skills-for-the-organisation}

Ein Skill bringt dem Assistenten bei, eine bestimmte Art von Aufgabe so zu erledigen, wie Ihre Organisation es
tut: eine Checkliste für Reviews, ein Berichtsformat, die Schritte eines Vorfallsberichts. Benutzer erstellen
ihre eigenen Skills im Chat und teilen sie; als Administrator sehen Sie jeden Skill, veröffentlichen Skills für
alle und entscheiden, wer was darf.

Wie Benutzer Skills im Chat verwenden und erstellen, steht unter [Skills](../use/skills.md). Das Skill-Format
selbst (die Datei `SKILL.md` und ihr Ordner) ist auf der Developers-Website beschrieben:
[Skills](https://developers.fadenstack.com/skills/).

## Bevor Sie beginnen {#before-you-start}

- Das Modul **Skills** ist eingeschaltet (**Einstellungen → Module**; siehe [Module](index.md#modules)).
- **Erweiterungen → Skills Hub** listet jeden Skill auf. Ihre eigenen Skills erreichen Benutzer über das
  Benutzermenü des Chats (**Fähigkeiten**).

## Eigentümer und wer einen Skill sieht {#owners-and-who-sees-a-skill}

Jeder Skill hat einen Eigentümer: die Person, die ihn erstellt hat, oder die Organisation (angezeigt als
**Gehört der Organisation**). Nur der Eigentümer oder ein Administrator kann einen Skill teilen, ändern, wer ihn
sieht, ihn archivieren oder löschen.

| **Wer sie sieht** | Wer ihn finden und nutzen kann |
|---|---|
| **Privat** | Der Eigentümer, die Personen und Gruppen, mit denen er geteilt ist, und Administratoren. Jeder neue Skill beginnt hier. |
| **Organisation** | Alle in der Organisation. |
| **Alle (alle Mandanten)** | Jeder Mandant auf dieser Installation. Nur für Administratoren. |

Einen privaten Skill können Sie auf seinem Reiter **Freigabe** mit Personen und Gruppen teilen, auf einer von
zwei Stufen:

| Stufe | Erlaubt |
|---|---|
| **Darf nutzen** | Die veröffentlichte Version sehen und in Chats nutzen. |
| **Darf bearbeiten** | Außerdem neue Versionen speichern und veröffentlichen. |

![Der Reiter Freigabe eines Skills: Wer sie sieht mit Privat, Organisation (ausgewählt) und Alle (alle Mandanten) sowie Geteilt mit, wo Personen und Gruppen hinzugefügt werden](../images/administer/skill-sharing.png)

Dieselben Regeln entscheiden, was in einen Chat gelangt: Ein privater Skill erreicht nie den Chat einer Person,
mit der er nicht geteilt ist. Verlässt jemand eine Gruppe, erreichen die mit dieser Gruppe geteilten Skills
seine Chats innerhalb einer Minute nicht mehr.

## Einen Skill für alle erstellen {#create-a-skill-for-everyone}

1. Öffnen Sie **Erweiterungen → Skills Hub** und wählen Sie **Fähigkeit erstellen** (oder **Importieren**, um
   eine `SKILL.md` oder einen gezippten Skill-Ordner zu importieren).

    ![Der obere Teil von Skills Hub: die Suche, der Filter Alle, Importieren und Fähigkeit erstellen, darunter die Spalten Eigentümer, Wer sie sieht, Wann sie genutzt wird und Status](../images/administer/skills-hub-toolbar.png)

2. Geben Sie **Fähigkeitsname** und **Kurzname** ein. Den Kurznamen tippen Benutzer im Chat nach `$`, um den
   Skill auszuwählen.
3. Schreiben Sie die **Beschreibung** (was der Skill tut und wann er zu verwenden ist: Das Modell liest sie) und
   die Anweisungen.
4. Wählen Sie unter **Wann sie genutzt wird**:
    - **Wenn ausgewählt**: nur wenn jemand ihn in einem Chat auswählt oder mit `$name` anfordert;
    - **Wenn sie passt**: wenn eine Nachricht eines seiner Auslöser-Schlüsselwörter enthält oder der Chat eine
      Datei eines seiner Dateitypen hat, und immer, wenn er ausgewählt wird;
    - **Immer**: in jedem Chat aller, die ihn sehen.
5. Wählen Sie unter **Wer sie sieht** die Option **Organisation**.
6. Speichern Sie ihn und veröffentlichen Sie ihn (siehe [Versionen](#versions)).

!!! note "„Immer“ für die ganze Organisation"
    Ein Skill, den alle sehen, gelangt in jeden Chat, wenn er auf **Immer** steht; deshalb kann das nur ein
    Administrator einstellen, auf dem Reiter **Einstellungen** des Skills. Nutzen Sie das für wenige, kurze
    Skills.

Der Reiter **Einstellungen** enthält außerdem die Tags, die Auslöser und die Tool-Listen des Skills: Tools, die
der Skill nutzen darf, bevorzugt oder nicht nutzen darf, und die Wissensquellen, auf die er zurückgreift.

## Versionen {#versions}

Jedes Speichern erzeugt eine neue Version; keine Version wird je geändert, und keine wird gelöscht, solange es den
Skill gibt. Ein Skill verweist auf
zwei davon:

- die neueste Version, die der Editor zeigt;
- die **veröffentlichte** (live geschaltete) Version, die Chats erhalten.

**Speichern** fügt eine Version hinzu, ohne zu ändern, was Chats erhalten. **Speichern und veröffentlichen**
oder **Veröffentlichen** bei einer Version ändert, was Chats erhalten. Auf dem Reiter **Versionen** vergleicht
**Änderungen** zwei Versionen, und **Hierauf zurücksetzen** veröffentlicht eine ältere, mit ihren Auslösern und
Tool-Listen. Der Status des Skills zeigt, was live ist, z. B. „Live v3 · Entwurf v4“.

![Der Reiter Versionen eines Skills: Version 1, live, mit Datum und Fingerabdruck](../images/administer/skill-versions.png)

Jede Version hat einen Fingerabdruck ihres Inhalts. Das Anfrageprotokoll hält für jede Anfrage, die einen
Skill genutzt hat, den Skill, die Version und diesen Fingerabdruck fest; so lässt sich nachschlagen, was dem Modell
gesagt wurde, solange der Skill nicht gelöscht ist.

Speichern zwei Personen denselben Skill gleichzeitig, wird das zweite Speichern mit „This skill changed since
you opened it“ abgelehnt, statt das erste zu überschreiben.

## Die Skills der Organisation verwalten {#manage-the-organisations-skills}

In **Erweiterungen → Skills Hub**:

- **Alle**, **Organisation**, **Meine** und **Mit mir geteilt** grenzen die Liste ein. Jeder Skill zeigt seinen
  Eigentümer, seinen Status und wie oft er in den letzten 30 Tagen genutzt wurde.
- **v… veröffentlichen** veröffentlicht die neueste Version eines Skills; **Archivieren** nimmt ihn aus der
  Nutzung und bewahrt ihn auf (**Wiederherstellen** holt ihn zurück); **Löschen** entfernt ihn endgültig.
- **Exportieren** liefert den **Fähigkeitsordner (.zip)** oder **Nur SKILL.md**, zum Aufbewahren oder zur
  Nutzung in anderen Tools.
- Der Reiter **Zuweisungen** bringt einen Skill in die Chats eines Mandanten, Arbeitsbereichs, Projekts oder
  Assistenten – für die Personen, die ihn sehen können.

Fadenstack führt die Skripte, die ein Skill-Ordner enthalten kann, nie aus. Sie werden aufbewahrt, damit der
Skill in Tools exportiert werden kann, die sie ausführen.

## Wer was darf {#who-may-do-what}

| Berechtigung | Erlaubt | Integrierte Rollen, die sie haben |
|---|---|---|
| `skills:read` | Die für sie sichtbaren Skills sehen und nutzen | `default_user`, `viewer`, `operator`, `admin` |
| `skills:create` | Eigene Skills erstellen und importieren | `default_user`, `operator`, `admin` |
| `skills:share` | Eigene Skills mit Personen und Gruppen teilen | `default_user`, `operator`, `admin` |
| `skills:publish` | Einen Skill für die ganze Organisation sichtbar machen | `admin` |
| `skills:manage` | Jeden Skill verwalten | `admin` |

Superuser haben sie alle. Um jemandem, der kein Administrator ist, das Veröffentlichen von Skills für alle zu
erlauben, geben Sie ihm eine Rolle mit `skills:publish` (siehe
[Benutzer, Gruppen und Rollen](users.md#roles-of-your-own)).

## Grenzen {#limits}

Sofern der Betreiber des Servers sie nicht geändert hat:

| Grenze | Standard |
|---|---|
| Veröffentlichte Skills, die die ganze Organisation sieht | 25 |
| Skills, die einer Person gehören | 200 |
| Für eine Nachricht ausgewählte Skills | 5 |
| Anweisungen einer Version | 100.000 Zeichen |
| Dateien im Ordner eines Skills | 64, je höchstens 512 KiB und insgesamt 4 MiB |

Private Skills zählen nicht zu den 25.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**Ein Skill kommt nicht in einen Chat.** Prüfen Sie, ob er veröffentlicht ist (nicht nur gespeichert), ob die
Person ihn sehen kann und wann er genutzt wird: Ein Skill auf **Wenn ausgewählt** kommt nur dazu, wenn er
ausgewählt wird.

**„Sie haben keine Berechtigung, Fähigkeiten zu teilen.“** Den Rollen der Person fehlt `skills:share`.

**Benutzer sehen die Seite mit den Skills nicht.** Das Modul **Skills** ist ausgeschaltet, oder ihren Rollen
fehlt `skills:read`.
