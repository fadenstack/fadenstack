# Netzwerk und Ports {#network-and-ports}

Welche Verbindungen eine Installation aufbaut, zwischen welchen Maschinen, auf welchen Ports, und welche davon
verschlüsselt sind. Nutzen Sie diese Seite, um Firewalls einzurichten und um zu entscheiden, welche Netzwerke sich
die Maschinen teilen.

## Auf einen Blick {#at-a-glance}

| Von | Nach | Port | Was darüber läuft | Verschlüsselt |
|---|---|---|---|---|
| Browser, API-Clients | Server | 443 (HTTPS), 80 (leitet auf HTTPS um) | Die Konsole, die API, das Gateway (`/v1`) | Ja, sobald HTTPS an ist |
| GPU-Maschinen | Server | 443, oder 80, solange HTTPS aus ist | Die Verbindung des Agents: Befehle, Status, Downloads des Agents, der Runtime-Images und der Modelle, Logs | Ja, sobald HTTPS an ist |
| Server | Head-Maschine jedes Clusters | Der Serving-Port des Clusters (standardmäßig 8010) | Jeder Prompt und jede Antwort, vom Gateway zu den Modellen | Ja, immer (gegenseitiges TLS) |
| Server | Eine Maschine mit einem vLLM, das Sie über Fadenstack routen | Der Port dieses vLLM plus 20000 | Prompts und Antworten | Ja, immer (gegenseitiges TLS) |
| Server | Cluster-Maschinen | Die Metrik-Ports von Ray | Metriken: nur Zahlen | Nein |
| Cluster-Maschinen | Untereinander, im Netzwerk des Clusters | Der Head-Port von Ray (standardmäßig 6390) und Ports, die Ray selbst wählt | Steuerung des Clusters, Daten, Aufrufe an Modellkopien auf anderen Maschinen (Prompts eingeschlossen), das zwischen Maschinen kopierte Runtime-Image | Immer authentifiziert; verschlüsselt, sobald **Verkehr zwischen den Maschinen** für den Cluster an ist |
| Cluster-Maschinen | Untereinander, im Netzwerk des Clusters | GPU-zu-GPU (RDMA) | Die Daten eines Modells, solange sich eine Kopie über mehrere Maschinen erstreckt | Nein: lässt sich nicht verschlüsseln |
| Server | Das Internet | 443 | Abrufen von Images, Modell-Downloads, externe Provider und MCP-Server, die Sie anbinden | Ja |

Die Maschinen öffnen keinen Port, über den der Server sie steuert: Ihre Agenten bauen die Verbindung zum Server
selbst auf und halten sie offen. Damit Modelle Anfragen beantworten können, muss der Server die Head-Maschine des
Clusters jedoch auf ihrem Serving-Port erreichen, und die Maschinen eines Clusters müssen einander direkt erreichen.
Eine Maschine hinter NAT kann keinem Cluster beitreten, dessen andere Maschinen auf der anderen Seite des NAT stehen.

Wie jede dieser Verbindungen geschützt ist und was Sie dafür einschalten: [HTTPS und Vertrauen](https.md).

## Benutzer und der Server {#users-and-the-server}

Nur der Eingang des Servers ist vom Netzwerk aus erreichbar: Port 443 für HTTPS und Port 80, der nur noch auf HTTPS
umleitet, sobald HTTPS an ist. Die Konsole, die API (`/api`), das OpenAI-kompatible Gateway (`/v1`) und Grafana
(`/grafana/`) sind alle dort erreichbar.

Um andere Ports zu verwenden:

```bash
faden ports                            # zeigt sie und die Adressen, unter denen die Konsole erreichbar ist
faden ports --http 8080 --https 8443   # ändert sie; der Proxy wechselt sofort
```

Sie stehen als `FADEN_HTTP_PORT` und `FADEN_HTTPS_PORT` in `~/fadenstack/.env`. Maschinen und API-Clients brauchen
dann den neuen Port in der Adresse des Servers.

Alles andere auf dem Server lauscht nur auf der Loopback-Adresse und ist vom Netzwerk aus nicht erreichbar:
Postgres (5432), Redis (6379), RabbitMQ (5672, 15672), ClickHouse (8123, 9000), MinIO (9090, 9091), Prometheus
(9099), Loki (3100), Grafana (3001), Langfuse (3002), der eigene Port des Gateways (8001) und einige weitere. Diese
Ports müssen auf dem Server selbst trotzdem frei sein; `faden doctor` listet jeden Port auf, den die Installation
verwendet, und ob ein anderes Programm ihn belegt.

!!! warning
    Geben Sie diese Ports nicht im Netzwerk frei. Loki hat keine eigene Authentifizierung, und der eigene Port von
    Grafana zeigt jedem, der ihn erreicht, alle Dashboards. Die Konsole erreicht beide über den Eingang.

## Die Maschinen und der Server {#the-machines-and-the-server}

Jede Maschine muss den Eingang des Servers erreichen, unter der Adresse, die Sie in
**Maschinen → Maschine hinzufügen** auswählen. Der Bereich listet die Adressen des Servers mit der jeweils
zugehörigen Netzwerkschnittstelle auf; wählen Sie eine im Netzwerk, das die Maschine mit dem Server teilt. Der Server
selbst baut keine Verbindungen zum Agent auf.

Für zwei Dinge baut der Server allerdings Verbindungen zu den Maschinen auf:

- **Die Modelle.** Das Gateway sendet jede Anfrage an die Head-Maschine des Clusters, der das Modell bereitstellt,
  auf dem Serving-Port des Clusters. Erlauben Sie dem Server, diesen Port auf jeder Head-Maschine zu erreichen.
- **Metriken.** Das Prometheus des Servers sammelt Metriken von den Maschinen des Clusters. Blockiert eine Firewall
  das, zeigt die Cluster-Seite keine Metriken; das Beantworten von Anfragen ist nicht betroffen.

## Zwischen den Maschinen eines Clusters {#between-the-machines-of-a-cluster}

Ein Cluster läuft in einem Netzwerk, das alle seine Maschinen teilen: Sie bestätigen es beim Anlegen des Clusters
und können es auf der Seite des Clusters unter **Erweitert** ändern. In diesem Netzwerk müssen die Maschinen einander
ungehindert erreichen:

- der Head von Ray lauscht auf dem Head-Port des Clusters (6390 bei einem neuen Cluster);
- weitere Ports wählt Ray selbst, für seine Knotenverwalter, seine Datenübertragung und seine Worker-Prozesse;
- eine Maschine, die das Runtime-Image braucht, erhält es über dieses Netzwerk von einer Maschine des Clusters, die
  es hat;
- GPU-zu-GPU-Verkehr (RDMA, z. B. RoCE zwischen NVIDIA-DGX-Spark-Maschinen) läuft ebenfalls hier.

Filtern Sie in diesem Netzwerk keine Ports zwischen den Maschinen. Beschränken Sie das Netzwerk selbst auf die
Maschinen des Clusters: ein direktes Kabel, ein eigener Switch oder ein VLAN. GPU-zu-GPU-Verkehr lässt sich nicht
verschlüsseln, und der eigene Verkehr von Ray ist erst verschlüsselt, wenn der Schalter
**Verkehr zwischen den Maschinen** des Clusters an ist.

Eine Maschine, die zu einem laufenden Cluster hinzugefügt wird, wird zuerst geprüft: Sie muss den Head im Netzwerk
des Clusters erreichen (siehe [Cluster](clusters.md#add-machines-to-a-running-cluster)).

## Ports, die auf einer GPU-Maschine frei sein müssen {#ports-a-gpu-machine-must-have-free}

| Port | Auf | Für |
|---|---|---|
| Der Head-Port des Clusters (standardmäßig 6390) | der Head-Maschine | den Head von Ray |
| Der Serving-Port des Clusters (standardmäßig 8010) | der Head-Maschine | die Modelle, hinter dem Proxy, der nur das Gateway durchlässt |
| Der Serving-Port plus 20000 (standardmäßig 28010) | der Head-Maschine, nur Loopback | die Modelle selbst |

Das Dashboard von Ray lauscht nur auf der Loopback-Adresse der Head-Maschine (8265). Belegt etwas auf einer Maschine
bereits einen dieser Ports, kann der Cluster nicht starten, und die Cluster-Seite zeigt den Fehler von Ray, der mit
`Address already in use` endet. Geben Sie den Port frei und wählen Sie dann **Erneut versuchen**. Ein Modell, das
auf einem Port veröffentlicht wird, den ein anderes Programm belegt, wird vor dem Start abgelehnt: „Port … is already
in use on this machine by another program“.

## Was ins Internet geht {#what-reaches-the-internet}

Siehe [Systemanforderungen](requirements.md#what-needs-the-internet). Kurz gesagt: Der Server lädt Images und Modelle
herunter; die Maschinen brauchen nichts aus dem Internet, wenn der Server die Agent-Builds bereithält.
