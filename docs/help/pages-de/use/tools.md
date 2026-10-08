# Tools im Chat {#tools-in-the-chat}

Mit Tools kann das Modell mehr als schreiben: etwas in einem Dienst nachschlagen, den Ihre Organisation nutzt, ein Ticket lesen, Dateien durchsuchen oder etwas für Sie ändern. Diese Seite zeigt, wie Sie Tools in einem Chat einschalten und wie Sie die Kontrolle darüber behalten, was sie tun.

![Der Tool-Bereich: die Reiter „Tools“ und „Datenschutz“, „Ausführung“ auf „Server Only“, die Schaltflächen für den Tool-Modus „Aus“, „Nur lesen“, „Fragen“ und „Automatisch“, wobei „Fragen“ gewählt ist, und die Tools eines Dienstes zum Anhaken, jedes als „nicht eingestuft“ und „fragt zuerst“ markiert](../images/use/chat-tools-panel.png)

## Welche Tools es gibt {#what-tools-there-are}

- **Tools, die Ihr Administrator angebunden hat.** Jeder Dienst (ein Ticketsystem, eine Code-Plattform, ein Dateispeicher, eine Suchmaschine) bringt eine Gruppe von Tools mit. Welche Dienste es gibt, entscheidet Ihr Administrator; wie sie angebunden werden, steht unter [MCP-Server](../administer/mcp-servers.md).
- **Tools, die immer da sind.** Das Modell kann das aktuelle Datum und die Uhrzeit lesen, unter den Tools, die Ihr Chat verwenden darf, nach einem passenden suchen und die Wissensdatenbank Ihrer Organisation sowie lange Dateien durchsuchen, die Sie angehängt haben (siehe [Wissen](knowledge.md)). Diese Tools lesen nur.

Jedes Tool hat eine Klasse, die angibt, was es tut:

| Klasse | Bedeutung |
|---|---|
| **lesen** | Liest nur; ändert nichts |
| **schreiben** | Ändert etwas, das sich zurückändern lässt |
| **zerstörend** | Löscht etwas oder tut etwas, das sich nicht rückgängig machen lässt |
| **nicht eingestuft** | Niemand hat bisher festgelegt, was es tut |

## Die Tools öffnen {#open-the-tools}

![Das Nachrichtenfeld: links Schraubenschlüssel, Puzzleteil und Büroklammer, rechts die Auswahl „Modell“ und die Senden-Schaltfläche](../images/use/chat-composer.png)

Wählen Sie den Schraubenschlüssel neben dem Nachrichtenfeld. Der Bereich, der sich öffnet, hat zwei Reiter: **Tools** und **Datenschutz** für den Schutz personenbezogener Daten in diesem Chat (siehe [Ihre Daten und Datenschutz](privacy.md)).

## Tools einschalten {#switch-tools-on}

Die Tools der Dienste sind in jedem Chat zunächst ausgeschaltet.

1. Öffnen Sie den Tool-Bereich.
2. Setzen Sie bei einem Tool einen Haken, um es einzuschalten, oder bei einer Gruppe, um alle ihre Tools einzuschalten.

Eingeschaltete Tools werden dem Modell mit jeder Nachricht angeboten. Die Zahl neben **Tools** zeigt, wie viele eingeschaltet sind.

??? info "Mehr zum Einschalten von Tools"
    - Sie können Tools schon vor Ihrer ersten Nachricht anhaken; sie gelten für den Chat, sobald er beginnt.
    - Sie müssen nicht alles einschalten, was Sie vielleicht brauchen. Passt keines seiner Tools zu Ihrer Frage, kann das Modell unter den Tools, die Ihr Chat verwenden darf, nach einem suchen und es selbst einschalten. Es bleibt dann für den Rest des Chats eingeschaltet, und der Bereich zeigt es angehakt. Entfernen Sie den Haken, um es wieder auszuschalten.
    - Ein Tool mit einem Hinweis darunter lässt sich gerade nicht verwenden, und der Hinweis sagt, warum: z. B. ist sein Dienst nicht erreichbar, Ihre Organisation erlaubt es nicht, oder der Tool-Modus schließt es aus.

## Den Tool-Modus wählen {#choose-the-tool-mode}

Der **Tool-Modus** legt fest, was Tools in diesem Chat tun dürfen, ohne Sie zu fragen:

| Modus | Was geschieht |
|---|---|
| **Aus** | In diesem Chat werden keine Tools verwendet, auch nicht die Wissensdatenbank. |
| **Nur lesen** | Nur Tools, die lesen, werden verwendet; die anderen werden nicht angeboten. |
| **Fragen** | Tools, die lesen, laufen; jedes andere Tool wartet auf Ihre Zustimmung. |
| **Automatisch** | Tools, die lesen oder schreiben, laufen; zerstörende und nicht eingestufte Tools warten auf Ihre Zustimmung. |

Jeder Chat beginnt mit **Fragen**. Sie können den Modus ändern, sobald der Chat begonnen hat; er bleibt dann bei diesem Chat. Bei einem Tool, das zuerst fragt, steht in der Liste **fragt zuerst**.

Kein Modus führt ein zerstörendes oder nicht eingestuftes Tool aus, ohne Sie zu fragen.

??? info "Warum viele Tools zuerst fragen"
    Die Tools vieler Dienste sind nicht eingestuft, bis Ihr Administrator festlegt, was sie tun: Sie fragen immer, und **Nur lesen** lässt sie weg.

## Ein Tool zulassen {#approve-a-tool}

Wenn ein Tool Ihre Zustimmung braucht, hält die Antwort an, und eine Karte zeigt **… möchte ausgeführt werden** mit dem, was das Tool gleich tun wird.

| Wählen Sie | Was geschieht |
|---|---|
| **Zulassen** | Das Tool wird ausgeführt. Die Antwort geht mit seinem Ergebnis weiter. |
| **Ablehnen** | Das Tool wird nicht ausgeführt; das Modell erfährt das und macht ohne es weiter. |

??? info "Mehr zum Zulassen von Tools"
    - Wenn Sie die Karte einige Minuten lang nicht beantworten, wird das Tool nicht ausgeführt.
    - Ihre Organisation kann außerdem Regeln für einzelne Tools haben: Manche werden nie ausgeführt, manche fragen immer zuerst. Wenn eine Regel ein Tool stoppt, sehen Sie, während die Antwort geschrieben wird, **Nicht ausgeführt** mit dem Namen des Tools und dem Grund.

## Ausführung {#execution}

**Ausführung**, über der Tool-Liste, legt fest, woher Tools kommen. Lassen Sie die Einstellung auf **Server Only**, sofern Ihr Administrator nichts anderes sagt.

??? info "Mehr zur Ausführung"
    | Einstellung | Tools |
    |---|---|
    | **Server Only** (die Voreinstellung) | Die Tools der Dienste, die Ihre Organisation angebunden hat |
    | **Local Only**, **Hybrid** | Für Tools auf Ihrem eigenen Computer; sie funktionieren nur, wenn eine Anwendung, die Ihre Organisation bereitstellt, sie anbietet |

## Was Tools erhalten {#what-tools-receive}

Ein Tool erhält, was das Modell ihm übergibt: oft einen Suchbegriff, eine Ticketnummer oder Text aus Ihrem Gespräch. Ihr Administrator legt für jeden Dienst fest, wie personenbezogene Daten in diesen Aufrufen behandelt werden. Siehe [Ihre Daten und Datenschutz](privacy.md).

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **Keine Tools verfügbar. Prüfen Sie, ob die MCP-Dienste eingerichtet sind und laufen.** | Für Sie ist kein Dienst angebunden, oder keiner ist erreichbar. Wenden Sie sich an Ihren Administrator. |
| Das Modell verwendet ein Tool nicht, das Sie eingeschaltet haben | Nicht jedes Modell kann Tools verwenden. Versuchen Sie ein anderes Modell, oder schreiben Sie in Ihrer Nachricht, welches Tool es verwenden soll. |
| **Nicht ausgeführt** mit dem Namen eines Tools | Der Tool-Modus oder eine Regel Ihrer Organisation hat die Ausführung verhindert. Ändern Sie den Tool-Modus, wenn der Grund darauf hinweist; fragen Sie andernfalls Ihren Administrator. |
