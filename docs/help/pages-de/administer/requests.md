# Was mit einer Anfrage geschieht {#what-happens-to-a-request}

Jede Nachricht, die jemand sendet – aus dem Chat, über einen Agenten oder aus einer Anwendung über die API –,
durchläuft dasselbe Gateway: die eine OpenAI-kompatible Adresse Ihres Fadenstack-Servers. Diese Seite erklärt in
einfachen Worten, was Fadenstack mit ihr macht. So können Sie Ihren Kolleginnen und Kollegen, Ihrem
Datenschutzbeauftragten oder Ihrem Betriebsrat sagen, was gilt.

## Sechs Dinge geschehen mit jeder Anfrage {#six-things-happen-to-every-request}

### 1. Fadenstack prüft, wer fragt {#1-fadenstack-checks-who-is-asking}

Eine Anfrage kommt mit der Anmeldung des Benutzers, einem API-Token, das er erstellt hat, oder der Anmeldung eines
Agenten auf seinem Gerät. Eine Anfrage ohne eines davon oder mit einem, das widerrufen wurde oder abgelaufen ist,
wird abgelehnt. Eine Anfrage über einen Agenten wird außerdem abgelehnt, wenn der Benutzer für diesen Agenten
nicht freigegeben ist oder der Agent stillgelegt wurde.

Eingerichtet unter: [Benutzer, Gruppen und Rollen](users.md) und [Agenten und Add-ins](agents.md).

### 2. Die Anfrage erhält, was das Modell braucht {#2-the-request-gets-what-the-model-needs}

Mit der neuen Nachricht gehen mit: die früheren Nachrichten des Chats, die Anweisungen des Projekts, zu dem der
Chat gehört, die [Skills](skills.md), die zutreffen (die der Benutzer ausgewählt hat und die, die von selbst
hinzukommen sollen), der Text angehängter Dateien und die Tools, die der Chat nutzen darf. Die Anfrage eines
Agenten bringt mit, was die Anwendung sendet: die Seite oder das Dokument, das der Agent liest, und die Tools der
Anwendung. Nur Skills, die der Benutzer sehen kann, gelangen in seine Anfragen.

### 3. Ihre Regeln werden angewendet {#3-your-rules-are-applied}

- **Personenbezogene Daten.** Wenn Ihre [Richtlinie](policies.md#personal-data) das Modell erfasst, das antworten
  wird, werden personenbezogene Daten maskiert, bevor die Anfrage Fadenstack verlässt. Wenn der Filter nicht
  laufen kann, entscheidet die Richtlinie, ob die Anfrage abgelehnt wird.
- **Tools.** Die [Tool-Regeln](policies.md#tool-rules), die Klassen der Tools und der Tool-Modus des Chats
  entscheiden, welche Tools dem Modell überhaupt angeboten werden und welche davon ohne Rückfrage laufen dürfen.

Inhaltsrichtlinien, die eine Eingabe oder eine Antwort anhand ihres Inhalts ablehnen, sind
<span class="fs-planned">Geplant</span>.

### 4. Das gewählte Modell antwortet {#4-the-model-you-chose-answers}

![Observability → Datenresidenz: wie viele Daten lokal blieben und wie viele nach außen gingen, und jeder Provider unter „Lokal (On-Premises)“ oder „Extern“](../images/administer/data-residency.png)

Die Anfrage nennt ein Modell: den Namen, der im Chat gewählt, im Agenten eingestellt oder von der Anwendung
angegeben wurde. Dieser Name ist entweder eine Ihrer [Bereitstellungen](deployments.md), die auf Ihren eigenen
Maschinen läuft, oder ein [externer Provider](providers.md).

??? info "Mehr dazu, welche Bereitstellung antwortet"
    Bieten mehrere Bereitstellungen denselben Namen an, antwortet eine davon, die funktionsfähig ist. Ein Modell,
    das nicht antwortet, erhält keine Anfragen, bis es wieder antwortet.

Wo das Modell läuft, bestimmt, wohin die Anfrage geht: [Datenresidenz](providers.md#data-residency) zeigt das für
jedes Modell.

### 5. Tools laufen nach Ihren Regeln {#5-tools-run-under-your-rules}

Wenn das Modell ein Tool anfordert, führt Fadenstack es aus, sofern die Regeln es erlauben, fragt zuerst den
Benutzer, wo die Regeln das vorsehen, und gibt das Ergebnis an das Modell zurück. Ergebnisse durchlaufen dieselben
Regeln für personenbezogene Daten. Tools, die zu einer Anwendung gehören, etwa das Ausfüllen eines Formulars im
Browser, laufen in dieser Anwendung auf dem Computer des Benutzers, mit denselben Freigaben. Die Antwort wird dann
an den Benutzer gestreamt; maskierte Werte werden dabei wieder eingesetzt, wo die Richtlinie es erlaubt.

### 6. Fadenstack zeichnet auf, was geschehen ist, nicht was gesagt wurde {#6-fadenstack-records-what-happened-not-what-was-said}

| Aufzeichnung | Was sie enthält |
|---|---|
| Das Anfrageprotokoll | Wer gefragt hat, welches Modell und wo es lief, wie viele Tokens, wie lange es gedauert hat, die geschätzten Kosten und das Ergebnis, und welche Version welches Skills verwendet wurde. Nicht den Text. Siehe [Nutzung und Limits](usage.md). |
| Der Chat | Die Unterhaltung selbst, gespeichert mit dem Chat des Benutzers auf dem Server und dort verschlüsselt. Agenten speichern ihre Unterhaltungen auf dem Computer des Benutzers. |
| Das Audit-Protokoll | Wer in der Konsole was geändert hat. |
| Governance-Entscheidungen | Was eine Regel abgelehnt oder nach Rückfrage durchgelassen hat. Nie den abgelehnten Text. |
| Das Datenschutzprotokoll | Wie viele personenbezogene Daten gefunden wurden, nach Art. Nie den Text. |

## Worauf sich Benutzer verlassen können {#what-people-can-rely-on}

- Ein privater Skill gelangt nie in die Anfrage eines anderen Benutzers.
- Tools laufen nur innerhalb des Modus, den der Benutzer gewählt hat, und der Regeln, die Sie festgelegt haben.
  Kein Modus führt ein zerstörendes oder nicht eingestuftes Tool aus, ohne nachzufragen.
- Ein Agent antwortet nur den Benutzern, für die er freigegeben ist, und zwar mit dem Modell, das seine
  veröffentlichte Version festlegt. Wenn seine Version die Tools der Anwendung auflistet, wird jedes andere Tool
  zurückgehalten.
- Die Aufzeichnung dessen, was eine Regel abgelehnt hat, enthält nie den abgelehnten Text.

Für die Benutzer von Fadenstack wird dasselbe unter [Ihre Daten und Datenschutz](../use/privacy.md) erklärt.
