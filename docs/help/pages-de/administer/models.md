# Modelle {#models}

Im **Modell-Marktplatz** wählen Sie die Modelle aus, die Ihre Organisation auf ihren eigenen Maschinen betreibt.
Er zeigt Modelle von Hugging Face, sagt zu jedem, ob es auf Ihren Cluster passt, hält die Modelle vor, die dieser
Server heruntergeladen hat, und stellt ein Modell in einem einzigen Dialog auf einem Cluster bereit. Modelle, die
bei einem Cloud-Provider laufen, binden Sie stattdessen unter [Externe Provider](providers.md) an.

![Der Modell-Marktplatz auf „Empfohlen“: „Passend für Cluster“ mit einem Cluster aus zwei GPUs, der Hugging-Face-Chip, „Nur passende Modelle zeigen“ und Modellkarten mit ihren Markierungen und „Bereitstellen“](../images/administer/marketplace.png)

## Bevor Sie beginnen {#before-you-start}

- Mindestens ein Cluster, für die Prüfung, ob ein Modell passt, und für die Bereitstellung (siehe
  [Cluster](../operate/clusters.md)). Ohne Cluster können Sie trotzdem stöbern.
- Für zugangsbeschränkte Modelle ein Hugging-Face-Token (siehe
  [Zugangsbeschränkte Modelle](#gated-models-and-the-hugging-face-token)).

## Ein Modell finden {#find-a-model}

Öffnen Sie **KI-Infrastruktur → Modell-Marktplatz**. Er hat drei Tabs:

| Tab | Zeigt |
|---|---|
| **Empfohlen** | Modelle, die erfahrungsgemäß gut funktionieren, gruppiert nach Zweck: **Zum Ausprobieren**, **Allgemeiner Chat**, **Programmieren**, **Schlussfolgern**, **Bilder** und **Embeddings**. |
| **Hugging Face durchsuchen** | Jedes Modell auf dem Hugging Face Hub. |
| **Auf diesem Server** | Die Modelle, die dieser Server bereits vorhält, und ihre Downloads (siehe [Auf diesem Server](#on-this-server)). |

Jede Karte zeigt, wofür das Modell gedacht ist (**Tools**, **Schlussfolgern**, **Bilder**, **Code**,
**Embeddings**) und wie es passt. Wählen Sie eine Karte für die Einzelheiten: Parameter, Kontextlänge,
Genauigkeit, Lizenz, seine Dateien und wie es auf jeden Ihrer Cluster passt. **Auf Hugging Face öffnen** zeigt
die vollständige Modellkarte.

??? info "Mehr zur Suche und zu den Karten"
    - Ein Modell mit der Markierung **Hier getestet** wurde auf der Hardware bereitgestellt, die sein Tooltip
      nennt.
    - Unter **Hugging Face durchsuchen** filtern Sie nach Aufgabe (**Chat**, **Embeddings**, **Bilder**) und
      **Autor** (eine Organisation oder ein Benutzer, z. B. `Qwen`), und sortieren nach **Im Trend**, **Meiste
      Downloads**, **Meiste Likes** oder **Neueste**. Im Suchfeld steht `*` für beliebigen Text und `?` für ein
      Zeichen: `qwen3.8*fp8` findet jeden FP8-Build von Qwen3.8.
    - Modelle, die die Engine nicht ausführen kann, sind ausgeblendet; eine Schaltfläche zeigt sie mit dem Grund
      an: GGUF-Dateien (für llama.cpp und Ollama), MLX-Gewichte (nur Apple Silicon), Repositorys ohne Gewichte
      und Modelle, die weder Chat- noch Bild- noch Embedding-Modelle sind.

## Welche Modelle passen {#which-models-fit}

Wählen Sie unter **Passend für Cluster** einen Cluster. Jede Karte zeigt dann, wie das Modell darauf passt:

| Markierung | Bedeutung |
|---|---|
| **Teilt sich eine GPU** | Klein genug, dass sich mehrere Kopien oder andere Modelle einen Beschleuniger teilen. Jede Kopie belegt nur ihren Anteil am Speicher. |
| **Passt auf N GPUs** | Eine Kopie braucht N Beschleuniger und wird auf sie aufgeteilt. |
| **Passt, aber nicht gerade jetzt** | Es passt auf den Cluster, aber andere Modelle belegen den Speicher, den es braucht. |
| **Zu groß** | Selbst alle Beschleuniger des Clusters zusammen können es nicht aufnehmen. |
| **Größe unbekannt** | Hugging Face gibt nicht an, wie groß es ist. |

**Nur passende Modelle zeigen** blendet die übrigen aus.

??? info "Wie geschätzt wird, ob ein Modell passt"
    Ob ein Modell passt, wird geschätzt: aus der Größe des Modells, dem Speicher, den seine Unterhaltungen
    brauchen, und dem Speicher jedes Beschleunigers. Der Bereitstellungsdialog zeigt die Zahlen, bevor etwas
    startet.

Bevor etwas heruntergeladen wird, prüft der Bereitstellungsdialog außerdem, ob die Runtime des Clusters das Modell
laden kann:

| Der Dialog zeigt | Bedeutung |
|---|---|
| **Läuft hier nicht** | Die Runtime kennt diese Art von Modell nicht. Der Cluster lässt sich nicht auswählen, und es wird nichts heruntergeladen. Das Modell braucht eine neuere Runtime auf dem Cluster (siehe [Einen Cluster auf eine andere Runtime umstellen](../operate/upgrading.md#move-a-cluster-to-another-runtime)). |
| **Läuft vielleicht nicht** | Die Runtime kann das Modell lesen, hat aber keine Implementierung seiner Architektur. Es kann trotzdem über einen allgemeinen Ausweichweg funktionieren, den nicht jedes Modell unterstützt. |

## Ein Modell bereitstellen {#host-a-model}

![Der erste Schritt des Bereitstellungsdialogs, „Wo“: der Cluster mit seiner Passform und der Zahl der Kopien, für die er Platz hat, die „Größe jeder Kopie“ und „Kopien“](../images/administer/host-where.png)

Wählen Sie auf der Karte **Bereitstellen**. Der Dialog hat drei Schritte:

| Schritt | Was Sie festlegen |
|---|---|
| **Wo** | Den Cluster, die Größe jeder Kopie und wie viele Kopien. |
| **Engine-Einstellungen** | Wie die Engine (vLLM) das Modell ausführt, ausgehend von vorgeschlagenen Einstellungen (siehe [Rezepte und vorgeschlagene Einstellungen](#recipes-and-suggested-settings)). |
| **Prüfen** | Den Namen der Bereitstellung und den Namen, den die Benutzer im Chat auswählen. |

[Bereitstellungen](deployments.md) beschreibt jeden Schritt und die Seite der Bereitstellung.

Der Server lädt das Modell herunter, kopiert es auf die Maschinen des Clusters und startet es. Sie müssen im
Dialog nicht warten: Die Seite der Bereitstellung zeigt jeden Schritt.

## Zugangsbeschränkte Modelle und das Hugging-Face-Token {#gated-models-and-the-hugging-face-token}

Öffentliche Modelle brauchen kein Token. Zugangsbeschränkte Modelle (darunter Llama, Gemma und einige
Mistral-Modelle) brauchen ein Token eines Hugging-Face-Kontos, das die Lizenz des Modells akzeptiert hat. Ein
Token erhöht außerdem die Download-Limits.

![Hugging-Face-Zugang: angemeldet als alex-example mit einem gespeicherten Token, ein Feld zum Ersetzen, „Prüfen und speichern“ und „Token entfernen“](../images/administer/marketplace-hf-token.png)

1. Wählen Sie oben im Marktplatz den Chip **Hugging Face** (oder öffnen Sie **Einstellungen → Allgemein**).
2. Erstellen Sie auf Hugging Face ein Lese-Token.
3. Fügen Sie es unter **Zugriffstoken** ein.
4. Wählen Sie **Prüfen und speichern**.

Der Chip zeigt dann das Konto, z. B. **Hugging Face: alex-example**. **Token entfernen** entfernt es;
zugangsbeschränkte Modelle lassen sich dann nicht mehr herunterladen.

??? info "Mehr zum Token"
    - Der Server prüft das Token bei Hugging Face, bevor er es speichert, und lehnt eines ab, das Hugging Face
      zurückweist. Ist Hugging Face nicht erreichbar, wird das Token gespeichert und als ungeprüft markiert.
    - Das Token wird verschlüsselt auf dem Server gespeichert und nie wieder angezeigt, auch nicht
      Administratoren. Darf es auch auf Hugging Face schreiben, schlägt der Dialog vor, es durch ein reines
      Lese-Token zu ersetzen.
    - Nur der Server verwendet es, zum Suchen und zum Herunterladen. Die Maschinen der Cluster erhalten ihre
      Modelle vom Server und sehen das Token nie, es sei denn, ihr Cluster lässt sie Modelle selbst
      herunterladen (siehe unten).

## Woher die Maschinen Modelle beziehen {#where-the-machines-get-models}

![Die Seite eines Clusters, nach unten gescrollt zu ihren Karten, darunter „Woher die Maschinen Modelle bekommen“ mit eingeschaltetem „Maschinen laden Modelle selbst herunter“ und jeder Maschine als „Erreicht Hugging Face“ markiert](../images/operate/cluster-advanced.png)

| | Wie | Internetzugang der Maschinen |
|---|---|---|
| **Standardmäßig** | Der Server lädt ein Modell einmal herunter, und jede Maschine des Clusters kopiert es vom Server. Über eine langsame Verbindung zwischen Server und Maschinen dauert ein großes Modell dann zweimal so lange. | Nicht nötig. |
| **Maschinen laden Modelle selbst herunter** (ein Schalter auf der Seite des Clusters) | Jede Maschine lädt Modelle selbst von Hugging Face herunter, bei einem zugangsbeschränkten Modell mit dem Token des Servers. Der Schalter gilt für das nächste Modell, das auf den Cluster kommt. | Jede Maschine muss Hugging Face erreichen. |

Siehe [Cluster](../operate/clusters.md#where-the-machines-get-models).

## Rezepte und vorgeschlagene Einstellungen {#recipes-and-suggested-settings}

Wenn das vLLM-Projekt ein Rezept für ein Modell veröffentlicht, sind dessen empfohlene Einstellungen Teil der
Vorschläge im Schritt **Engine-Einstellungen**, und die Details des Modells weisen darauf hin (**Rezept öffnen**).
Für andere Modelle werden die Einstellungen passend zur Modellfamilie vorgeschlagen. Jeder Vorschlag nennt seinen
Grund.

??? info "Mehr zu Rezepten"
    Einstellungen aus dem Rezept, die hier nicht verwendet werden können, werden aufgelistet; optionale
    Funktionen aus dem Rezept erscheinen als Auswahl. Ein Server ohne Internetzugang hat keine Rezepte und
    schlägt Einstellungen nach Modellfamilie vor.

Die Engine-Einstellungen selbst sind unter [Bereitstellungen](deployments.md#engine-settings) beschrieben.

## Auf diesem Server {#on-this-server}

Der Tab **Auf diesem Server** listet die Modelle auf, die dieser Server vorhält.

![„Auf diesem Server“: wie viele Modelle der Server vorhält und ihre Größe, „Nach Modellen suchen, die schon auf diesem Server liegen“ und „Aus einem Pfad hinzufügen“, und zwei Modelle mit „Bereit“, „Verwendet von“, „Details“, „Delete“ und „Bereitstellen“](../images/administer/marketplace-on-server-top.png)

| Auf dem Tab | Was es tut |
|---|---|
| **Downloads** | Laufen auf dem Server, nicht in der Seite: Wenn Sie die Seite verlassen oder den Browser schließen, laufen sie weiter. Jeder Download zeigt seinen Fortschritt und lässt sich abbrechen (**Abbrechen**) oder erneut versuchen (**Erneut versuchen**). Solange etwas heruntergeladen wird, zeigt ein Chip in der Kopfzeile das auf jedem Tab an. |
| **Auf diesen Server herunterladen** (in den Details eines Modells) | Lädt es herunter, ohne es bereitzustellen; so liegt es bereit, wenn jemand es bereitstellt. |
| **Aus einem Pfad hinzufügen** | Trägt einen Ordner auf dem Fadenstack-Server ein, der bereits die Dateien eines Modells enthält (seine `config.json` und die Gewichte). Fadenstack löscht diese Dateien nie. |
| **Nach Modellen suchen, die schon auf diesem Server liegen** | Übernimmt Modelle, die auf anderem Weg in den Modellspeicher des Servers kopiert wurden. |
| **Verwendet von** | Listet die Bereitstellungen auf, die das jeweilige Modell verwenden. |
| **Löschen** | Entfernt ein Modell aus der Liste. Ein Modell, das eine Bereitstellung noch verwendet, lässt sich nicht löschen: Der Dialog nennt die Bereitstellungen, die Sie zuerst entfernen müssen. **Auch seine Dateien auf diesem Server löschen** gibt den Speicherplatz frei; ein aus einem Pfad hinzugefügtes Modell behält seine Dateien. |

## Ohne Internetzugang {#without-internet-access}

Ein Server, der Hugging Face nicht erreicht, zeigt trotzdem die Liste der Empfehlungen und die Modelle, die er
vorhält, mit geschätzten Größen. Die Suche ist nicht verfügbar. Sie können Modelle bereitstellen, die bereits auf
dem Server liegen, auch solche, die aus einem Pfad hinzugefügt wurden.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **„Hugging Face: Token abgelehnt“** | Hugging Face akzeptiert das gespeicherte Token nicht mehr: Es ist vielleicht abgelaufen oder wurde widerrufen. Speichern Sie ein neues. |
| Ein zugangsbeschränktes Modell wird nicht heruntergeladen | Das Token fehlt, oder sein Konto hat die Lizenz des Modells auf Hugging Face nicht akzeptiert. Akzeptieren Sie sie dort und versuchen Sie den Download dann unter **Auf diesem Server** erneut. |
| **„Dieser Server verwendet noch den Standard-Hauptschlüssel für Einstellungen“** | Der Server speichert kein Token, solange er keinen eigenen Schlüssel hat. Wer den Server betreibt, legt einen fest (siehe [Installation](../operate/installing.md)). |
| **Fehlgeschlagene Modell-Downloads** | Das [Dashboard](dashboard.md) listet die fehlgeschlagenen Downloads des letzten Tages auf. Öffnen Sie **Auf diesem Server**, lesen Sie den Grund und wählen Sie **Erneut versuchen**. Eine Bereitstellung, die auf das Modell wartet, übernimmt es, sobald der Download abgeschlossen ist. |
