# Cluster {#clusters}

Ein Cluster fasst eine oder mehrere GPU-Maschinen zusammen, damit sie gemeinsam Modelle bereitstellen. Sie erstellen
ihn in der Konsole, er startet auf seinen Maschinen, und danach können Sie ihm Maschinen hinzufügen, eine Maschine
leeren, eine herausnehmen, seine Runtime wechseln und ihn stoppen oder starten. Modelle stellt ein Administrator auf
einem Cluster bereit (siehe [Bereitstellungen](../administer/deployments.md)).

![KI-Infrastruktur → Cluster: ein laufender Cluster mit zwei Maschinen und zwei Beschleunigern, und oben „Cluster erstellen“](../images/operate/clusters.png)

## Bevor Sie beginnen {#before-you-start}

- Die Maschinen sind hinzugefügt und haben den Status **healthy** (siehe [GPU-Maschinen](machines.md)).
- Maschinen, die sich einen Cluster teilen sollen, erreichen einander über ein Netzwerk. Siehe
  [Netzwerk und Ports](network.md#between-the-machines-of-a-cluster).
- Ein Modell läuft jeweils auf einer Art von GPU. Maschinen verschiedener Art können sich einen Cluster teilen, aber
  keine Kopie eines Modells erstreckt sich über beide Arten. Wenn Maschinen einander nicht erreichen oder ohnehin
  verschiedene Modelle ausführen sollen, erstellen Sie für jede einen eigenen Cluster: Das Gateway schickt Anfragen an
  den Cluster, der das Modell bereitstellt.

## Einen Cluster erstellen {#create-a-cluster}

1. Öffnen Sie **KI-Infrastruktur → Cluster** und wählen Sie **Cluster erstellen**.

    ![Der Assistent „Cluster erstellen“ im ersten Schritt, „Benennen“, gefolgt von „Maschinen wählen“ und „Netzwerk bestätigen“](../images/operate/cluster-create-name.png)

2. **Benennen**: Geben Sie unter **Clustername** einen Namen ein, z. B. `gpu-room`, und wählen Sie **Weiter**.

    ![„Cluster erstellen“, Schritt „Maschinen wählen“: jede Maschine mit Adresse, Beschleunigern und Zustand, hier beide als bereits in einem anderen Cluster gekennzeichnet](../images/operate/cluster-create-machines.png)

3. **Maschinen wählen**: Markieren Sie die Maschinen, die Modelle bereitstellen sollen. Bei mehr als einer Maschine
   wählen Sie **Welche Maschine den Cluster leitet** (den Head). Wählen Sie **Weiter**.
4. **Netzwerk bestätigen**: Prüfen Sie das Netzwerk, das die Konsole vorschlägt: Eine VPN-Schnittstelle kann wie
   eine dedizierte Verbindung aussehen. Wählen Sie ein anderes Netzwerk, wenn der Vorschlag nicht das Netzwerk ist,
   auf dem der Cluster laufen soll.
5. Wählen Sie **Cluster erstellen**.

??? info "Mehr zur Wahl der Maschinen und des Netzwerks"
    Ein Cluster aus einer einzigen Maschine ist völlig in Ordnung; weitere können Sie später hinzufügen. Zu jeder
    Maschine nennt der Assistent die Runtime, die sie ausführen wird. Als Head vorgeschlagen wird die Maschine mit
    den meisten Beschleunigern.

    Bei **Netzwerk bestätigen** listet die Konsole die Netzwerke auf, die die Maschinen gemeinsam haben, testet sie
    und schlägt das schnellste vor, das nicht mit dem Verwaltungsverkehr geteilt wird.

Die Seite des Clusters öffnet sich im Zustand **Wird gestartet** und zeigt einen Balken pro Maschine, während die
Maschinen das Runtime-Image holen. Der Cluster erreicht **Läuft**: Jede Maschine ist aktiv, ihre Beschleuniger sind
gezählt, und die Seite bietet **Modell bereitstellen** an. Sind die Summen niedriger als erwartet, ist eine Maschine
nicht beigetreten: Öffnen Sie sie und lesen Sie ihren Status.

??? info "Was die Maschinen tun, während der Cluster startet"
    1. Sie holen das Runtime-Image und prüfen es. Es kommt einmal vom Server; die erste Maschine, die es hat, gibt
       es über das Netzwerk des Clusters an die anderen weiter. Eine abgebrochene Übertragung wird dort fortgesetzt,
       wo sie stehen blieb, wenn die Maschine Platz für eine Kopie des Images auf der Festplatte hat.
    2. Sie starten darin Ray: zuerst den Head, dann treten die anderen Maschinen bei.
    3. Sie melden, was sie vorgefunden haben.

| Sie sehen | Was Sie tun |
|---|---|
| Eine Maschine in der Liste ist als bereits in einem anderen Cluster gekennzeichnet | Nehmen Sie sie aus diesem Cluster heraus, bevor Sie sie wählen. |
| „Auf … läuft kein zertifiziertes Runtime-Image“ | Für diese Art von Maschine gibt es keine Runtime; nehmen Sie sie aus der Auswahl. |
| **„Diese Maschinen können noch keinen Cluster bilden.“** | Der Netzwerkschritt hat kein Netzwerk gefunden, das alle gemeinsam haben. Prüfen Sie Verkabelung und Adressen und versuchen Sie es dann erneut. |

## Die Seite des Clusters {#the-clusters-page}

Öffnen Sie **Cluster** und wählen Sie den Cluster.

![Die Seite eines laufenden Clusters: oben sein Zustand und die Aktionen, die Übersicht und wie seine zwei Maschinen verbunden sind](../images/operate/cluster-page.png)

| Teil | Was er enthält |
|---|---|
| Oben | Sein Zustand, **Jetzt prüfen** (den Cluster sofort prüfen, statt auf die nächste Prüfung zu warten), **Stopp** oder **Start**, **Maschinen hinzufügen**, **Modell bereitstellen** und **Löschen**. |
| Eine Übersicht | Aktive **Maschinen**, **Beschleuniger**, **Freie Beschleuniger**, **Bereitstellungen**. |
| **So ist dieser Cluster verbunden** | Die Maschinen und wie sie miteinander verbunden sind. Wählen Sie eine Maschine, um zu sehen, was sie ausführt, und um **Leeren** oder **Aus Cluster entfernen** zu wählen. |
| **Modelle auf diesem Cluster** | Jede Bereitstellung mit ihrem Zustand und ihren Kopien. |
| Karten | Für **Laufzeit**, **Modellverkehr**, **Verkehr zwischen den Maschinen** und **Woher die Maschinen Modelle bekommen**. |
| **Erweitert** | Die **Zustandsprüfungen**, die der Server ausgeführt hat, **Netzwerk ändern** und der Rohstatus. |

![Weiter unten auf der Seite eines Clusters: die Karten „Modellverkehr“, „Laufzeit“, „Verkehr zwischen den Maschinen“ und „Woher die Maschinen Modelle bekommen“, „Modelle, die hier ohne Deployment laufen“, und „Erweitert“ geöffnet bei den Zustandsprüfungen](../images/operate/cluster-advanced.png)

| Zustand des Clusters | Bedeutung |
|---|---|
| **Wird gestartet** | Die Runtime kommt auf die Maschinen, Ray wird gestartet |
| **Läuft** | Alle Maschinen sind aktiv; Modelle können bereitgestellt werden |
| **Braucht Aufmerksamkeit** | Etwas stimmt nicht, und die Seite sagt, was; Fadenstack prüft oder repariert es (siehe [Zustandsprüfungen](#health-checks-and-repairs)) |
| **Fehlgeschlagen** | Er konnte nicht starten oder nicht repariert werden; die Seite nennt den Grund |
| **Gestoppt** | Absichtlich gestoppt; seine Maschinen und sein Netzwerk bleiben erhalten |

## Stoppen, starten und löschen {#stop-start-and-delete}

| Aktion | Was sie tut |
|---|---|
| **Stopp** | Stoppt den Cluster auf seinen Maschinen und gibt ihre GPUs frei. Maschinen, Netzwerk und Bereitstellungen bleiben erhalten. |
| **Start** | Bringt ihn zurück und wendet seine Modelle erneut an. |
| **Löschen** | Entfernt den Cluster. Ein Cluster, der noch Bereitstellungen hat, kann nicht gelöscht werden: Löschen Sie diese zuerst, damit kein Modell auf den Maschinen weiterläuft. Ein laufender Cluster wird zuerst gestoppt und dann gelöscht; die Maschinen bleiben registriert und können einem anderen Cluster beitreten. |

??? info "Einen Cluster löschen, dessen Maschinen alle offline sind"
    Sind alle Maschinen des Clusters offline, lässt er sich von hier aus nicht stoppen; er wird trotzdem gelöscht,
    und was er auf einer Maschine hinterlassen hat, wird ersetzt, wenn diese Maschine das nächste Mal einen Cluster
    startet.

## Maschinen zu einem laufenden Cluster hinzufügen {#add-machines-to-a-running-cluster}

![Maschinen zu gpu-cluster hinzufügen: hier gibt es noch keine Maschine außerhalb des Clusters, und jede Maschine wird zuerst geprüft](../images/operate/cluster-add-machines.png)

1. Wählen Sie auf der Seite des Clusters **Maschinen hinzufügen**.
2. Markieren Sie die Maschinen, die hinzukommen sollen. Maschinen, die nicht beitreten können, sind mit dem Grund
   aufgeführt und lassen sich nicht markieren.
3. Wählen Sie **Hinzufügen**.

Die bereits laufenden Kopien bleiben, wo sie sind. Neue Kopien und neue Modelle können die neue Maschine nutzen; sie
erhält die Dateien der Modelle, wenn sie sie braucht. Um ein Modell auf die neue Maschine zu bringen, skaliert ein
Administrator dessen Bereitstellung hoch; eine Änderung der Anzahl der Kopien startet die bereits laufenden Kopien
nicht neu (siehe [Bereitstellungen](../administer/deployments.md)).

| Angezeigter Grund | Was zu tun ist |
|---|---|
| It is offline. | Starten Sie die Maschine oder ihren Agent |
| It is in maintenance. | **Wartung beenden** auf der Seite der Maschine |
| It is being drained. | Beenden Sie zuerst das Leeren der Maschine |
| It belongs to the cluster … ; remove it there first. | Nehmen Sie sie aus dem anderen Cluster |
| It has no address on the cluster's network (…). | Verbinden Sie die Maschine mit diesem Netzwerk oder geben Sie ihr dort eine Adresse |
| The cluster runs Ray with TLS, which needs agent … or later on it. | Aktualisieren Sie den Agent der Maschine (führen Sie die Installationszeile erneut aus) |
| *Maschine* could not reach the cluster's head on … | Die Maschine hat eine Adresse im Netzwerk, erreicht den Head dort aber nicht: Prüfen Sie Verkabelung, Routen und Firewalls |

??? info "Mehr zum Hinzufügen von Maschinen"
    Jede Maschine wird zuerst geprüft: Sie muss den Head des Clusters im Netzwerk des Clusters erreichen. Dann
    erhält sie das Runtime-Image (von einer Maschine des Clusters, die es hat), startet ihren Container und tritt Ray
    bei, so wie die ersten Maschinen des Clusters.

    Eine Warnung, dass die Maschine von einer anderen Art ist als der Head (eine andere Architektur), ist keine
    Ablehnung: Sie führt ein anderes Runtime-Image aus, und keine Kopie eines Modells darf sich über beide Arten
    erstrecken.

## Eine Maschine leeren {#drain-a-machine}

Leeren zieht die Modellkopien einer Maschine auf die anderen Maschinen des Clusters um und hält neue Kopien von ihr
fern: vor einer Wartung, einem Neustart oder bevor Sie sie aus dem Cluster nehmen. Sie sehen zuerst einen Plan, und
nichts stoppt, ohne dass Sie zustimmen.

![Der Leeren-Plan für gpu-02: Eine Kopie stoppt, weil keine andere Maschine genug freie GPU hat, und die Schaltfläche lautet „Leeren und 1 stoppen“](../images/operate/cluster-drain-plan.png)

1. Wählen Sie auf der Seite des Clusters die Maschine und dann **Leeren**. (Oder **Leeren** auf der Seite der
   Maschine selbst oder in der Liste **Maschinen**.)
2. Lesen Sie den Plan. Für jede Kopie auf der Maschine steht dort entweder **Zieht um auf** mit dem Namen einer
   Maschine oder **Stoppt** mit dem Grund.
3. Wählen Sie **Leeren**. Würden Kopien stoppen, lautet die Schaltfläche **Leeren und … stoppen** mit deren Anzahl:
   Damit stimmen Sie dem zu.

Eine Kopie, die nicht umziehen kann, nennt den Grund:

| Grund im Plan | Bedeutung |
|---|---|
| The cluster has no other machine that can take it. | Keine andere Maschine ist aktiv und im Cluster |
| No other machine holds this model's files yet. | Die Kopie konnte nirgendwo sonst laden |
| It needs … GPU; the most another machine has free is … GPU. | Anderswo sind nicht genug Beschleuniger frei |
| It needs about … of GPU memory; the most another machine has free is … | Anderswo ist nicht genug GPU-Speicher frei |

??? info "Wann eine Kopie umzieht, und wann gestoppte Kopien zurückkommen"
    Eine Kopie zieht nur um, wenn eine andere Maschine des Clusters die Dateien des Modells hat und sowohl den
    Anteil an Beschleunigern als auch den GPU-Speicher frei hat, den die Kopie braucht. Jede umziehende Kopie
    bedient weiter Anfragen, bis ihr Ersatz auf der anderen Maschine läuft; nichts wird vorzeitig gestoppt.

    Kopien, deren Stopp Sie zugestimmt haben, starten von selbst wieder, sobald eine Maschine Platz hat, z. B.
    nachdem das Leeren beendet wurde.

Während das Leeren läuft, zeigt die Seite des Clusters es mit seinem Zustand: **Leeren beginnt**,
**Kopien ziehen um**, **Geleert**. Eine geleerte Maschine führt nichts aus und übernimmt nichts Neues; erledigen Sie
Ihre Arbeit an ihr.

**Leeren beenden** (auf der Seite des Clusters oder auf der Seite der Maschine) beendet es: Die Maschine verlässt Ray
und tritt wieder bei, und Kopien, die noch auf ihr sind, starten neu. Ray bietet keinen anderen Weg, ein Leeren
rückgängig zu machen.

Der Head kann nicht geleert werden, solange der Cluster läuft: Der Cluster kann ohne ihn nicht laufen. Stoppen Sie
stattdessen den Cluster oder leeren Sie die anderen Maschinen. Leeren braucht einen aktuellen Agent auf der Maschine
und auf dem Head; der Plan nennt die Maschinen, die aktualisiert werden müssen.

**Eine Kopie ist nach 30 Minuten nicht umgezogen.** Das Leeren meldet das („… have not moved after 30 minutes: the
replacement did not start“). Lesen Sie die Fehler der Bereitstellung oder beenden Sie das Leeren.

## Eine Maschine aus einem Cluster entfernen {#remove-a-machine-from-a-cluster}

1. Wählen Sie auf der Seite des Clusters die Maschine und dann **Aus Cluster entfernen**.
2. Lesen Sie den Plan: Es ist der Leeren-Plan, gefolgt vom Austritt der Maschine.
3. Wählen Sie **Entfernen** (oder **Entfernen und … stoppen** mit der Anzahl der Kopien, die stoppen würden).

Die Maschine wird zuerst geleert, dann verlässt sie Ray und den Cluster (**Verlässt den Cluster**). Sie bleibt
registriert und kann einem anderen Cluster beitreten. **Entfernen abbrechen** bricht den Vorgang ab, solange die
Maschine noch geleert wird.

Auf einem gestoppten Cluster läuft nichts, daher wird die Maschine nur aus dem Eintrag entfernt. Um den Head
herauszunehmen, stoppen Sie zuerst den Cluster.

## Zustandsprüfungen und Reparaturen {#health-checks-and-repairs}

Jede Minute sieht sich Fadenstack jeden laufenden Cluster und jedes aktive Modell an, und zusätzlich jedes Mal, wenn
sich eine Maschine wieder verbindet. Es startet nur dann etwas neu, wenn etwas nicht stimmt, und prüft vorher ein
zweites Mal. Was es selbst repariert und was Sie währenddessen sehen, steht in [Fehler und Logs](failures.md). Kurz
gesagt:

| Wenn | Was geschieht |
|---|---|
| Eine Maschine ist aus dem Cluster ausgefallen | Sie wird aus Ray genommen und tritt wieder bei; Kopien auf den anderen Maschinen bedienen weiter Anfragen. |
| Der Head ist gestoppt | Er wird mit dem ganzen Cluster neu gestartet, und die Modelle werden erneut angewendet. |
| Drei Neustarts sind fehlgeschlagen | Der Cluster zeigt **Fehlgeschlagen** und wird alle 15 Minuten erneut versucht. **Erneut versuchen** auf der Seite des Clusters beginnt sofort von vorn. |

**Erweitert → Zustandsprüfungen** listet die Prüfungen auf und was sie ergeben haben.

## Runtime {#runtime}

![Die Karte „Laufzeit“: die Runtime, mit der der Cluster läuft, ob sie zertifiziert ist, und „Umschalten auf“ mit „Umschalten“](../images/operate/cluster-runtime-card.png)

Die Karte **Laufzeit** zeigt das Image, das die Maschinen des Clusters ausführen, Ray und vLLM zusammen, und ob eine
neuere zertifizierte Runtime verfügbar ist. Ein Cluster behält seine Runtime, bis Sie sie wechseln; das startet jedes
Modell darauf neu. Siehe [Einen Cluster auf eine andere Runtime umstellen](upgrading.md#move-a-cluster-to-another-runtime).

## Verschlüsselung {#encryption}

![Die Karte „Modellverkehr“, „Verschlüsselt“: Das Gateway erreicht die Modelle des Clusters über gegenseitiges TLS](../images/operate/cluster-model-traffic.png)

**Modellverkehr**: Das Gateway erreicht die Modelle des Clusters über gegenseitiges TLS (mutual TLS). Ein Cluster,
der gestartet wurde, bevor es diesen Schutz gab, zeigt „unverschlüsselt“; **Modellverkehr verschlüsseln** stellt
seine Modelle hinter TLS, und sie laden neu.

![Die Karte „Verkehr zwischen den Maschinen“, „Verschlüsselt“, mit „Ausschalten“](../images/operate/cluster-traffic-encrypted.png)

**Verkehr zwischen den Maschinen**: **Verschlüsseln** schaltet TLS für Ray zwischen den Maschinen des Clusters ein.
Das startet den Cluster und seine Modelle neu; jede Maschine braucht einen aktuellen Agent.

Beides ist in [HTTPS und Vertrauen](https.md#model-traffic-the-gateway-and-the-machines) erklärt.

## Woher die Maschinen Modelle bekommen {#where-the-machines-get-models}

Standardmäßig lädt der Server ein Modell herunter, und jede Maschine kopiert es vom Server; die Maschinen brauchen
also keinen Internetzugang. Die Karte **Woher die Maschinen Modelle bekommen** kann einen Cluster auf **Maschinen
laden Modelle selbst herunter** umstellen: Jede Maschine lädt dann von Hugging Face in ihren eigenen Speicher, bei
einem zugangsbeschränkten Modell mit dem Hugging-Face-Token des Servers. Dafür muss jede Maschine Hugging Face
erreichen; die Karte prüft das und nennt alle, die es nicht können. Die Änderung gilt für das nächste Modell, das auf
den Cluster kommt.

## Modelle, die ohne Bereitstellung laufen {#models-running-without-a-deployment}

Eine Bereitstellung, die gelöscht wurde, während ihr Cluster nicht erreichbar war, kann ihr Modell weiterlaufen
lassen: Es belegt GPUs und antwortet weiterhin am Gateway. Wählen Sie unter **Modelle, die hier ohne Deployment
laufen** die Option **Danach suchen** und dann bei einem Modell **Übernehmen**, um es wieder zu verwalten: Behalten
Sie es, oder löschen Sie es ordnungsgemäß.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| Der Cluster startet nicht, mit `Address already in use` | Etwas auf einer Maschine belegt einen der Ports des Clusters. Geben Sie ihn frei (siehe [Netzwerk und Ports](network.md#ports-a-gpu-machine-must-have-free)) und wählen Sie dann **Erneut versuchen**. |
| **„This will not change by retrying …“** | Der Cluster wartet, statt es erneut zu versuchen, weil die Ursache jedes Mal gleich ausfällt; am häufigsten hält der Server einen anderen Build des Runtime-Images, als der Cluster braucht. Führen Sie auf dem Server `faden runtime-images --check` aus, holen Sie, was fehlt, und wählen Sie dann **Erneut versuchen**. |
| Andere Startfehler, in den Worten der Maschine angezeigt | Sie werden mit wachsenden Pausen erneut versucht. Beheben Sie die Ursache und wählen Sie **Erneut versuchen**, um es sofort erneut zu versuchen. |
| **„Die Maschinen haben das Stoppen nicht bestätigt.“** | Prüfen Sie, ob sie online sind, und stoppen oder löschen Sie dann erneut. |
