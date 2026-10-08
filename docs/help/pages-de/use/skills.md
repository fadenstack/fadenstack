# Skills {#skills}

Ein Skill bringt dem Assistenten bei, eine bestimmte Art von Aufgabe so zu erledigen, wie Ihr Team es tut: eine Checkliste für Prüfungen, den Aufbau eines Berichts, die Schritte eines Störungsberichts. Sie wählen ihn einmal in einem Chat aus, und das Modell folgt ihm dort bei jeder Nachricht. Diese Seite zeigt, wie Sie Skills nutzen und wie Sie aus einem Chat eigene Skills speichern.

![„Skills für diesen Chat“, geöffnet über das Puzzleteil: Skills zum Anhaken, darunter „Skill aus diesem Chat erstellen…“ und „Skills verwalten“](../images/use/chat-skills.png)

## Bevor Sie beginnen {#before-you-start}

Skills sind ein optionaler Teil von Fadenstack. Wenn der Chat neben dem Nachrichtenfeld keine Schaltfläche mit einem Puzzleteil hat, sind Skills für Ihre Organisation ausgeschaltet, oder Ihr Konto darf sie nicht nutzen; fragen Sie Ihren Administrator.

## Was ein Skill mit einer Antwort macht {#what-a-skill-does-to-a-reply}

Ein Skill ist eine Sammlung schriftlicher Anweisungen. Solange er ausgewählt ist, gehen sie mit jeder Ihrer Nachrichten an das Modell, sodass die Antwort ihnen folgt: den Schritten, dem Format, den Prüfungen, dem Ton.

??? info "Mehr dazu, was ein Skill tut"
    Ein Skill kann auch Dateien mitbringen, z. B. eine Vorlage, die das Modell lesen kann, wenn es sie braucht.

    Ein Skill erweitert nicht, was das Modell weiß oder was es tun darf: Dokumente kommen aus [Wissen](knowledge.md), Aktionen aus [Tools](tools.md). Ein Skill kann einige Tools aus dem Chat heraushalten, solange er genutzt wird.

## Einen Skill in einem Chat nutzen {#use-a-skill-in-a-chat}

![Das Nachrichtenfeld: das Puzzleteil neben Schraubenschlüssel und Büroklammer, und der Hinweis, dass $ einen Skill wählt und /skill einen erstellt](../images/use/chat-composer.png)

Beide Wege funktionieren, auch vor Ihrer ersten Nachricht:

- **Tippen Sie `$`** in das Nachrichtenfeld und dann einen Teil des Skill-Namens, z. B. `$incident`. Passende Skills werden schon während der Eingabe aufgelistet. Wählen Sie mit den Pfeiltasten einen aus und übernehmen Sie ihn mit der Eingabetaste oder der Tabulatortaste.
- **Wählen Sie das Puzzleteil** neben dem Nachrichtenfeld. **Skills für diesen Chat** listet die Skills auf, die Sie nutzen können; setzen Sie bei den gewünschten einen Haken.

Ein ausgewählter Skill erscheint als Chip über dem Nachrichtenfeld. Er wird für jede Nachricht in diesem Chat verwendet, bis Sie ihn mit dem × auf seinem Chip entfernen.

??? info "Mehr zu ausgewählten Skills"
    Jeder Chat hat seine eigene Auswahl, die in diesem Browser gespeichert wird: Auf einem anderen Computer wählen Sie die Skills erneut aus.

## Skills, die von selbst hinzukommen {#skills-that-come-in-on-their-own}

Manche Skills sind so eingerichtet, dass sie ohne Auswahl in einen Chat kommen: wenn Ihre Nachricht bestimmte Wörter enthält, wenn der Chat eine Datei eines bestimmten Typs hat, oder immer. Sie erscheinen nicht als Chip. Wenn eine Antwort einem Format folgt, um das Sie nicht gebeten haben, kann ein solcher Skill der Grund sein; Ihr Administrator kann Ihnen sagen, welche Skills Ihre Organisation auf diese Weise nutzt.

## Einen Skill aus einem Chat speichern {#save-a-skill-from-a-chat}

Wenn ein Gespräch so gelaufen ist, wie es beim nächsten Mal laufen soll, speichern Sie es als Skill:

1. Tippen Sie `/skill` in das Nachrichtenfeld und drücken Sie die Eingabetaste. Oder wählen Sie das Puzzleteil und dann **Skill aus diesem Chat erstellen…**.
2. Wählen Sie in **Neuer Skill** die Option **Aus diesem Chat entwerfen** oder **Selbst schreiben**.
3. Lesen Sie den Entwurf und ändern Sie, was nötig ist. Das Modell wird angewiesen, Namen, Passwörter und andere Einzelheiten des Gesprächs wegzulassen; prüfen Sie das aber selbst.
4. Legen Sie unter **Kurzname** einen Namen fest: Ihn tippen Sie nach `$`.
5. Wählen Sie **Skill speichern**.

Gespeichert wird erst, wenn Sie **Skill speichern** wählen. Neue Skills sind privat: Nur Sie und die Administratoren sehen sie, bis Sie sie teilen.

??? info "Mehr zum Speichern eines Skills"
    - Nach `/skill` können Sie ergänzen, was der Skill tun soll, z. B. `/skill review pull requests the way we just did`.
    - Mit **Aus diesem Chat entwerfen** liest das Modell des Chats das Gespräch und schreibt einen Entwurf: einen Namen, eine Beschreibung und die Anweisungen.
    - **Jetzt in diesem Chat nutzen** ist angehakt, sodass der Skill sofort ausgewählt ist; entfernen Sie den Haken, wenn Sie das nicht möchten.

## Ihre Skills {#your-skills}

![Die Seite „Skills“ mit den Reitern „Meine“, „Mit mir geteilt“, „Organisation“ und „Alle“, der Suche und den Schaltflächen „Importieren“ und „Skill erstellen“](../images/use/skills-page.png)

1. Wählen Sie Ihren Avatar in der oberen Leiste.
2. Wählen Sie **Skills**.

Die Seite listet die Skills, die Sie nutzen können, unter **Meine**, **Mit mir geteilt** und **Organisation** auf. Öffnen Sie einen, um seine Anweisungen zu lesen oder einen eigenen zu ändern. Jedes Speichern wird als Version aufbewahrt.

??? info "Teilen, Versionen und das Skill-Format"
    Wer einen Skill sehen kann, wie das Teilen funktioniert und wie Versionen veröffentlicht werden, steht unter [Skills für die Organisation](../administer/skills.md). Das Skill-Format, um Skills von Hand zu schreiben oder zu importieren, steht auf der Website Developers: [Skills](https://developers.fadenstack.com/skills/).

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| Die Liste nach `$` ist leer | Es gibt keinen Skill mit diesem Namen, den Sie nutzen können. Sehen Sie unter **Skills** in Ihrem Avatar-Menü nach, oder bitten Sie die Person, der der Skill gehört, ihn mit Ihnen zu teilen. |
| Die Antwort ignoriert den Skill | Prüfen Sie, ob sein Chip über dem Nachrichtenfeld steht. Kleinere Modelle folgen langen Anweisungen weniger zuverlässig; versuchen Sie ein größeres Modell. |
| **Sie haben keinen Zugriff auf Skills. Bitten Sie einen Administrator um die Berechtigungen dafür.** | Ihr Konto darf keine Skills nutzen. Fragen Sie Ihren Administrator. |
| **Das Modul „Skills“ ist ausgeschaltet. Ein Administrator kann es einschalten.** | Skills werden in Ihrer Organisation nicht genutzt. |
