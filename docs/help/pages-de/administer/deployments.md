# Bereitstellungen {#deployments}

Eine Bereitstellung ist ein Modell, das auf einem Ihrer Cluster läuft, in einer oder mehreren Kopien. Diese Seite
beschreibt, wie Sie ein Modell bereitstellen, die Seite einer Bereitstellung lesen, ändern, wie viele Kopien laufen
und wie die Engine sie ausführt, und was die Zustände und Meldungen bedeuten.

## Bevor Sie beginnen {#before-you-start}

- Ein Cluster, der läuft (siehe [Cluster](../operate/clusters.md)).
- Ein Modell: aus dem [Modell-Marktplatz](models.md) oder eines, das dieser Server bereits vorhält.

## Ein Modell bereitstellen {#host-a-model}

Beginnen Sie an einer dieser Stellen; alle öffnen denselben Dialog:

- **Bereitstellen** auf der Karte eines Modells unter **KI-Infrastruktur → Modell-Marktplatz**;
- **Modell bereitstellen** auf der Seite eines Clusters;
- **Modell bereitstellen** unter **KI-Infrastruktur → Deployments** (der Dialog beginnt dann mit dem Schritt
  **Modell**: Wählen Sie eines der Modelle, die dieser Server vorhält).

Dann:

1. **Wo.** Wählen Sie den **Cluster** und die **Größe jeder Kopie**: einen Anteil an einem Beschleuniger, einen
   Beschleuniger oder mehrere (die Kopie wird dann auf sie aufgeteilt). Legen Sie fest, wie viele **Kopien**
   laufen. Die Prüfung, ob das Modell passt, wählt eine Größe und sagt, für wie viele Kopien der Cluster Platz
   hat. Wählen Sie **Weiter**.

    ![Der Schritt „Wo“ des Bereitstellungsdialogs: der Cluster mit seiner Passform und der Zahl der Kopien, für die er Platz hat, die Größe jeder Kopie (ein Anteil an einem Beschleuniger, 1 GPU oder 2 GPUs) und „Kopien“](../images/administer/host-where.png)

2. **Engine-Einstellungen.** Die vorgeschlagenen Einstellungen passen für die meisten Zwecke (siehe
   [Engine-Einstellungen](#engine-settings)). Ändern Sie hier die längste Unterhaltung, wenn die Benutzer längere
   brauchen. Wählen Sie **Weiter**.
3. **Prüfen.** Prüfen Sie die Felder **Name der Bereitstellung** (Kleinbuchstaben, Ziffern und Bindestriche;
   erscheint in Listen und Logs) und **Im Chat anbieten als**: den Namen, den die Benutzer im Chat auswählen und
   an der API verwenden. **Revision (optional)** legt einen Branch, ein Tag oder einen Commit auf Hugging Face
   fest. Wählen Sie **Bereitstellen**.

Der Server lädt das Modell herunter, falls er es noch nicht hat, kopiert es auf die Maschinen und startet die
Kopien. Sie müssen im Dialog nicht warten: **Deployments** und die Seite der Bereitstellung zeigen, wie weit sie
ist. Wenn nicht jede Kopie in den gerade freien Speicher passt, sagt der Dialog das; die übrigen starten, sobald
andere Modelle Platz machen.

## Die Seite der Bereitstellung {#the-deployments-page}

Öffnen Sie **KI-Infrastruktur → Deployments** und wählen Sie eine Bereitstellung. Die Seite zeigt:

- **Zustand**: den Zustand der Bereitstellung (siehe [Zustände](#states)) und **Kopien (bereit / gewünscht)**.
- **Cluster** und **Modell**, **Zuletzt geprüft** und **Letzte Meldung**: was die Bereitstellung zuletzt
  gemeldet hat, in Worten. **Jetzt prüfen** sieht sofort nach, statt bis zur nächsten regulären Prüfung zu warten.
- **Im Chat**: den Namen, unter dem sie angeboten wird, oder „nicht angeboten: nur Endpunkt“.
- **Eine Antwort muss beginnen innerhalb von**: die Wartezeit auf die Antwort (siehe
  [Wartezeit auf die Antwort](#reply-wait)).
- **Engine-Einstellungen**: was gegenüber den Standardwerten der Engine geändert wurde.
- **Maschinen**: die Maschinen des Clusters und ihren Zustand. Welche Maschine welche Kopie ausführt, wird nicht
  angezeigt.
- **Modelldateien**: ob das Modell auf jeder Maschine liegt oder auf wie vielen, und eine Schaltfläche
  **Synchronisieren**.
- **Endpunkte**: die Adresse, unter der das Modell antwortet, sobald die erste Kopie bereit ist.
- Am Gateway gemessene Werte der letzten Minuten: **Anfragen**, **Generierungsgeschwindigkeit**, **Zeit bis zum
  ersten Token**, **Anfragelatenz** und **Fehlgeschlagen**; weiter unten die Werte unter **Von der Engine
  gemeldet**.
- **Protokolle**: ein gemeinsames Protokoll aller Maschinen des Clusters, mit zwei Quellen:
  **Modellbereitstellung** (Kopien starten, werden bereit, stoppen) und **Engine (vLLM)** (was die Engine
  ausgegeben hat, auch warum ein Start fehlgeschlagen ist). Filtern Sie nach Stufe, Quelle und Maschine, oder
  suchen Sie; kopieren Sie die Zeilen, die die Filter zeigen, oder laden Sie sie herunter.

![Die Seite einer Bereitstellung: Zustand „Aktiv“ mit 2 von 2 Kopien, „Letzte Meldung“, „Im Chat“ und die Wartezeit auf die Antwort mit „Ändern“, „Engine-Einstellungen“, „Maschinen“ und „Modelldateien“, oben „Skalieren“, „Jetzt prüfen“ und „Stopp“](../images/administer/deployment.png)

## Im Chat anbieten {#offer-it-in-the-chat}

Eine Bereitstellung wird im Chat nur unter einem Namen angeboten, den jemand gewählt hat. Der
Bereitstellungsdialog schlägt einen vor. So legen Sie ihn fest oder ändern ihn:

1. Wählen Sie auf der Seite der Bereitstellung **Im Chat anbieten** (oder **Ändern** neben **Im Chat**).
2. Geben Sie den Namen unter **Im Chat anbieten als** ein, oder leeren Sie das Feld, damit das Modell nur über
   seinen Endpunkt erreichbar ist.
3. Wählen Sie **Speichern**.

Das Modell läuft weiter; nur sein Name ändert sich. Es erscheint innerhalb von ein bis zwei Minuten im Chat.
Bereitstellungen mit demselben Namen teilen sich die Anfragen: Jede Anfrage geht an eine von ihnen, die
funktionsfähig ist.

## Wartezeit auf die Antwort {#reply-wait}

Ein ausgelastetes Modell hält neue Anfragen in seiner Warteschlange, bis es Platz hat, und sendet so lange nichts.
Nach Ablauf der Wartezeit schlägt die Anfrage fehl, und die Person kann sie erneut senden. Die Wartezeit beträgt
180 Sekunden, sofern Sie sie nicht ändern:

1. Wählen Sie **Ändern** neben **Eine Antwort muss beginnen innerhalb von**.
2. Geben Sie 10 bis 1800 Sekunden ein, oder lassen Sie das Feld leer für den Standard des Gateways.
3. Wählen Sie **Speichern**.

Das Modell läuft weiter, während Sie das ändern. Laufen Antworten häufig in die Zeitüberschreitung, braucht das
Modell mehr Platz statt mehr Geduld: mehr Kopien oder mehr Speicher pro Kopie.

## Skalieren {#scale-it}

**Skalieren** auf der Seite der Bereitstellung legt zwei Dinge fest:

- **Kopien**: wie viele Kopien laufen. Mehr Kopien bedienen mehr Anfragen gleichzeitig.
- **Beschleuniger pro Kopie** (bei einem Modell, das auf einem Beschleuniger läuft): 1 ist ein ganzer
  Beschleuniger. Mit weniger teilen sich Kopien einen Beschleuniger; mehr als 0,5 gibt jeder Kopie einen eigenen.
  Die Engine belegt denselben Anteil am Speicher des Beschleunigers (bei einem ganzen: 80 %), und der Dialog zeigt,
  wie viel das ist.

Wählen Sie **Anwenden**.

| Sie ändern | Was passiert |
|---|---|
| Die Zahl der Kopien | Die laufenden Kopien bedienen weiter Anfragen. Neue Kopien starten daneben; überzählige Kopien beenden ihre Anfragen und stoppen. Die Bereitstellung bleibt **Aktiv**, und **Letzte Meldung** sagt, was noch aussteht, z. B. „Serving on 1 of 2 copies; 1 more starting.“ |
| **Beschleuniger pro Kopie** | Jede Kopie startet mit ihrem neuen Anteil neu. Das Modell ist nicht verfügbar, bis sie wieder laufen. |

Der Dialog zählt die Beschleuniger des Clusters: „Jede Kopie belegt 1 Beschleuniger. Dieser Cluster hat 2, daher
passen 2 Kopien, wenn sonst nichts darauf läuft.“ Werden mehr verlangt, starten die Kopien, die passen, und
bedienen Anfragen, und die Seite sagt, warum die übrigen nicht starten können („… 1 cannot start: each copy needs
1 accelerator, and this cluster has 2, so 2 fit. Scale to 2, or add a machine to the cluster.“).

!!! note "Eine Kopie braucht auch Speicher"
    Eine Kopie braucht sowohl ihren Anteil an einem Beschleuniger als auch den Beschleunigerspeicher, den ihre
    Engine anfordert. Hat eine Maschine den Anteil frei, belegt aber ein anderes Modell oder Programm den
    Speicher, versucht die neue Kopie immer wieder zu starten: **Letzte Meldung** bleibt bei „Serving on 1 of 2
    copies; 1 more starting.“, und das Protokoll **Engine (vLLM)** zeigt den Speicherfehler der Engine. Die
    bereits laufenden Kopien bedienen weiter Anfragen. Skalieren Sie wieder herunter, senken Sie den
    Beschleunigeranteil oder den Speicheranteil der Engine (beides startet jede Kopie neu), stoppen Sie eine
    andere Bereitstellung auf dieser Maschine, oder fügen Sie dem Cluster eine Maschine hinzu.

Eine Bereitstellung, die über die API so eingerichtet ist, dass sie sich zwischen einer niedrigsten und einer
höchsten Zahl von Kopien selbst skaliert, startet jede Kopie neu, wenn sich diese Grenzen ändern. **Skalieren** in
der Konsole legt immer eine feste Zahl fest.

Eine Bereitstellung, die von einer früheren Fadenstack-Version gestartet wurde, startet jede Kopie einmal neu,
wenn sich nach dem Upgrade zum ersten Mal die Zahl ihrer Kopien ändert. Danach bedienen die laufenden Kopien
weiter Anfragen, wenn sich die Zahl ändert. Dazu braucht es aktuelle Agenten auf den Maschinen des Clusters und
eine aktuelle Runtime; ohne sie startet jede Änderung jede Kopie neu.

## Engine-Einstellungen {#engine-settings}

Die Engine (vLLM) führt das Modell aus. **Engine-Einstellungen → Ändern** auf der Seite der Bereitstellung öffnet
denselben Editor wie der Bereitstellungsdialog:

- Mit *empfohlen* markierte Werte stammen aus dem Rezept des vLLM-Projekts für das Modell, wenn es eines gibt,
  sonst aus Regeln für die Modellfamilie. Jeder Wert nennt den Grund für den Vorschlag.
- **Optimiert für**: **Ausgewogen**, **Lange Dokumente**, **Viele Nutzer** oder **Wenig Speicher**. Eine
  Voreinstellung ändert nur die Einstellungen, um die es ihr geht.
- **Kontext und Speicher**: **Längste Unterhaltung** (Prompt und Antwort zusammen), **Anteil am
  Beschleunigerspeicher**, **8-Bit-Kontextcache**.
- **Durchsatz**: **Gleichzeitige Anfragen**, **Gemeinsame Prompt-Anfänge wiederverwenden**, **Lange Prompts
  aufteilen**.
- **Was es kann**: **Tool-Aufrufe** und das zugehörige **Format**, **Denken getrennt anzeigen**, **Embeddings
  bereitstellen**. Ohne Tool-Aufrufe antwortet das Modell nur mit Text, und die im Chat angebotenen Tools
  (MCP-Tools, die Wissensdatenbank) werden nicht genutzt.
- **Kompatibilität**: **Graph-Erfassung überspringen**, **Eigenen Code des Modells ausführen**. Schalten Sie
  Letzteres nur für Herausgeber ein, denen Sie vertrauen: Es führt Python-Code aus dem Repository des Modells auf
  Ihren Maschinen aus.
- **Alle vLLM-Einstellungen**: jede Einstellung der Engine-Version, die der Cluster verwendet, durchsuchbar, und
  **Weitere Flags als Text** für alles andere. Alles, was aus dem Befehl ausbrechen könnte, wird abgelehnt.
- Eine Vorschau der Einstellungen, als vLLM-Flags dargestellt.

![Der Schritt „Engine-Einstellungen“ des Bereitstellungsdialogs: „Optimiert für“ mit seinen vier Voreinstellungen, „Kontext und Speicher“, „Durchsatz“, „Was es kann“ und „Kompatibilität“, mit markierten empfohlenen Werten](../images/administer/host-engine.png)

Vor einer Einstellung, mit der das Modell nicht starten wird, erscheinen Warnungen: eine Unterhaltung, die länger
ist, als das Modell unterstützt oder als in den Speicher passt, ein Speicheranteil über 95 %, ein Tool-Format bei
ausgeschalteten Tool-Aufrufen oder der eigene Code des Modells.

Der Speicheranteil lässt sich unter den Anteil am Beschleuniger senken, der unter **Skalieren** festgelegt ist,
aber nie darüber anheben; ein höherer Wert wird begrenzt, und der Dialog sagt das.

!!! warning
    **Speichern und neu starten** startet jede Kopie mit den neuen Einstellungen neu. Das Modell ist nicht
    verfügbar, bis sie wieder laufen. Startet eine Kopie nicht, nennt die Seite den Grund, und Sie können die
    Einstellungen zurücksetzen.

## Stoppen, starten und löschen {#stop-start-and-delete}

- **Stopp** auf der Seite der Bereitstellung nimmt die Kopien von den Maschinen und gibt ihre Beschleuniger frei;
  bei einem großen Modell kann das einige Minuten dauern. Die Bereitstellung behält ihre Einstellungen. **Start**
  lädt sie wieder.
- **Löschen** in den Aktionen der Zeile unter **Deployments** stoppt sie und entfernt sie aus ihrem Cluster. Das
  Modell bleibt auf diesem Server unter **Modell-Marktplatz → Auf diesem Server**, sodass es ohne erneuten
  Download wieder bereitgestellt werden kann.

## Zustände {#states}

| Zustand | Bedeutung |
|---|---|
| **In Warteschlange** | Wartet darauf, angewendet zu werden, z. B. bis ihr Cluster läuft. |
| **Modell wird kopiert** | Der Server lädt das Modell bei Bedarf herunter und kopiert es dann auf die Maschinen. |
| **Wird gestartet** | Die Kopien starten und laden das Modell. |
| **Aktiv** | Mindestens eine Kopie bedient Anfragen. |
| **Eingeschränkt** | Einige Kopien bedienen Anfragen, andere sind nicht gestartet oder fehlerhaft. Das Modell ist weiterhin nutzbar. |
| **Wird gestoppt**, **Gestoppt** | Wird gestoppt oder ist gestoppt. |
| **Fehlgeschlagen** | Sie ist nicht gestartet. **Letzte Meldung** und das Protokoll nennen den Grund. |
| **Entfernt** | Gelöscht. |

## Wenn es nicht funktioniert {#when-it-does-not-work}

**„Start failed: … Not tried again: this error comes back on every attempt.“** Die Engine hat mit einem Fehler
angehalten, der bei jedem Versuch wiederkäme, etwa Gewichte, die sie nicht laden kann, oder eine Einstellung, die
sie ablehnt. Wählen Sie **Fehler anzeigen**, um die Zeilen im Protokoll zu sehen. Meist passt das Modell nicht in
den Speicher des Beschleunigers, oder es braucht ein Zahlenformat, das die Karte nicht unterstützt. Beheben Sie
die Ursache (bei Speicher: eine kürzere **Längste Unterhaltung** oder den **8-Bit-Kontextcache**), und speichern
Sie dann die Änderung oder wählen Sie **Jetzt prüfen**: Der Start beginnt von vorn.

**„A start failed: … Ray is trying again.“** Ein Fehler, der vorübergehen kann, etwa Speicher, der noch nicht frei
ist, oder eine Zeitüberschreitung. Der Cluster versucht es von selbst weiter.

**„1 of 2 copies serve; the others failed to start: …“** (**Eingeschränkt**). Das Modell läuft auf weniger Kopien
als verlangt. Lesen Sie den Grund, und skalieren Sie dann herunter, geben Sie Speicher frei oder korrigieren Sie
die genannte Einstellung.

**„… Ray has not found room for it yet -- another deployment may be using the accelerators it needs.“** Stoppen
Sie eine andere Bereitstellung auf dem Cluster oder skalieren Sie sie herunter, oder fügen Sie eine Maschine
hinzu.

**Die Bereitstellung wartet, während der Server das Modell herunterlädt, und zeigt dann einen Download-Fehler.**
Zum Beispiel bei einem zugangsbeschränkten Modell ohne Hugging-Face-Token oder einem falsch geschriebenen
Repository. Beheben Sie das (siehe [Modelle](models.md#gated-models-and-the-hugging-face-token)) und wählen Sie
**Erneut versuchen** unter **Modell-Marktplatz → Auf diesem Server**. Die Bereitstellung übernimmt das Modell,
sobald es da ist.

**Sie läuft, ist aber nicht im Chat.** Sie hat keinen Chat-Namen: **Im Chat** zeigt „nicht angeboten: nur
Endpunkt“. Wählen Sie **Im Chat anbieten**.

**Antworten schlagen fehl mit „The model did not start its reply within … s; it may be busy.“** Das Modell war
länger ausgelastet, als die Wartezeit auf die Antwort erlaubt. Fügen Sie Kopien hinzu, geben Sie jeder Kopie mehr
Speicher oder erhöhen Sie die Wartezeit.

Mehr zu Fehlern und Logs: [Fehler und Logs](../operate/failures.md).
