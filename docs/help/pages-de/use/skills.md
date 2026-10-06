# Skills {#skills}

Ein Skill bringt dem Assistenten bei, eine bestimmte Art von Aufgabe so zu erledigen, wie Ihr Team es tut: eine Checkliste für Prüfungen, den Aufbau eines Berichts, die Schritte eines Störungsberichts. Sie wählen ihn einmal in einem Chat aus, und das Modell folgt ihm dort bei jeder Nachricht. Diese Seite zeigt, wie Sie Skills nutzen und wie Sie aus einem Chat eigene Skills speichern.

## Bevor Sie beginnen {#before-you-start}

Skills sind ein optionaler Teil von Fadenstack. Wenn der Chat neben dem Nachrichtenfeld keine Schaltfläche mit einem Puzzleteil hat, sind Skills für Ihre Organisation ausgeschaltet, oder Ihr Konto darf sie nicht nutzen; fragen Sie Ihren Administrator.

## Was ein Skill mit einer Antwort macht {#what-a-skill-does-to-a-reply}

Ein Skill ist eine Sammlung schriftlicher Anweisungen. Solange er ausgewählt ist, gehen sie mit jeder Ihrer Nachrichten an das Modell, sodass die Antwort ihnen folgt: den Schritten, dem Format, den Prüfungen, dem Ton. Ein Skill kann auch Dateien mitbringen, z. B. eine Vorlage, die das Modell lesen kann, wenn es sie braucht.

Ein Skill ändert nicht, was das Modell weiß oder was es tun darf. Dokumente kommen aus [Wissen](knowledge.md), Aktionen aus [Tools](tools.md).

## Einen Skill in einem Chat nutzen {#use-a-skill-in-a-chat}

Beide Wege funktionieren, auch vor Ihrer ersten Nachricht:

- **Tippen Sie `$`** in das Nachrichtenfeld und dann einen Teil des Skill-Namens, z. B. `$incident`. Passende Skills werden schon während der Eingabe aufgelistet. Wählen Sie mit den Pfeiltasten einen aus und übernehmen Sie ihn mit der Eingabetaste oder der Tabulatortaste.
- **Wählen Sie das Puzzleteil** neben dem Nachrichtenfeld. **Fähigkeiten für diesen Chat** listet die Skills auf, die Sie nutzen können; setzen Sie bei den gewünschten einen Haken.

![„Fähigkeiten für diesen Chat“, geöffnet über das Puzzleteil: Skills zum Anhaken, darunter „Fähigkeit aus diesem Chat erstellen…“ und „Fähigkeiten verwalten“](../images/use/chat-skills.png)

Ein ausgewählter Skill erscheint als Chip über dem Nachrichtenfeld. Er wird für jede Nachricht in diesem Chat verwendet, bis Sie ihn mit dem × auf seinem Chip entfernen. Jeder Chat hat seine eigene Auswahl, die in diesem Browser gespeichert wird: Auf einem anderen Computer wählen Sie die Skills erneut aus.

## Skills, die von selbst hinzukommen {#skills-that-come-in-on-their-own}

Manche Skills sind so eingerichtet, dass sie ohne Auswahl in einen Chat kommen: wenn Ihre Nachricht bestimmte Wörter enthält, wenn der Chat eine Datei eines bestimmten Typs hat, oder immer. Sie erscheinen nicht als Chip. Wenn eine Antwort einem Format folgt, um das Sie nicht gebeten haben, kann ein solcher Skill der Grund sein; Ihr Administrator kann Ihnen sagen, welche Skills Ihre Organisation auf diese Weise nutzt.

## Einen Skill aus einem Chat speichern {#save-a-skill-from-a-chat}

Wenn ein Gespräch so gelaufen ist, wie es beim nächsten Mal laufen soll, speichern Sie es als Skill:

1. Tippen Sie `/skill` in das Nachrichtenfeld und drücken Sie die Eingabetaste. Sie können ergänzen, was der Skill tun soll, z. B. `/skill review pull requests the way we just did`. Oder wählen Sie das Puzzleteil und dann **Fähigkeit aus diesem Chat erstellen…**.
2. Wählen Sie in **Neue Fähigkeit** die Option **Aus diesem Chat entwerfen**. Das Modell des Chats liest das Gespräch und schreibt einen Entwurf: einen Namen, eine Beschreibung und die Anweisungen. Oder wählen Sie **Selbst schreiben**.
3. Lesen Sie den Entwurf und ändern Sie, was nötig ist. Das Modell wird angewiesen, Namen, Passwörter und andere Einzelheiten des Gesprächs wegzulassen; prüfen Sie das aber selbst.
4. Legen Sie unter **Kurzname** einen Namen fest: Ihn tippen Sie nach `$`. Setzen Sie einen Haken bei **Jetzt in diesem Chat nutzen**, wenn der Skill sofort ausgewählt sein soll.
5. Wählen Sie **Fähigkeit speichern**.

Gespeichert wird erst, wenn Sie **Fähigkeit speichern** wählen. Neue Skills sind privat: Nur Sie sehen sie, bis Sie sie teilen.

## Ihre Skills {#your-skills}

Wählen Sie Ihren Avatar in der oberen Leiste und dann **Fähigkeiten**. Die Seite listet die Skills, die Sie nutzen können, unter **Meine**, **Mit mir geteilt** und **Organisation** auf. Öffnen Sie einen, um seine Anweisungen zu lesen oder einen eigenen zu ändern. Jedes Speichern wird als Version aufbewahrt.

![Die Seite „Fähigkeiten“ mit den Reitern „Meine“, „Mit mir geteilt“, „Organisation“ und „Alle“, der Suche und den Schaltflächen „Importieren“ und „Fähigkeit erstellen“](../images/use/skills-page.png)

Wer einen Skill sehen kann, wie das Teilen funktioniert und wie Versionen veröffentlicht werden, steht unter [Skills für die Organisation](../administer/skills.md). Das Skill-Format, um Skills von Hand zu schreiben oder zu importieren, steht auf der Website Developers: [Skills](https://developers.fadenstack.com/skills/).

## Wenn es nicht funktioniert {#when-it-does-not-work}

**Die Liste nach `$` ist leer.** Es gibt keinen Skill mit diesem Namen, den Sie nutzen können. Sehen Sie unter **Fähigkeiten** in Ihrem Avatar-Menü nach, oder bitten Sie die Person, der der Skill gehört, ihn mit Ihnen zu teilen.

**Die Antwort ignoriert den Skill.** Prüfen Sie, ob sein Chip über dem Nachrichtenfeld steht. Kleinere Modelle folgen langen Anweisungen weniger zuverlässig; versuchen Sie ein größeres Modell.

**Sie haben keinen Zugriff auf Fähigkeiten. Bitten Sie einen Administrator um die Berechtigungen dafür.** Ihr Konto darf keine Skills nutzen. Fragen Sie Ihren Administrator.

**Das Modul „Fähigkeiten“ ist ausgeschaltet. Ein Administrator kann es einschalten.** Skills werden in Ihrer Organisation nicht genutzt.
