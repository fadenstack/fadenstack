# Betreiben {#operate}

Dieser Abschnitt ist für alle, die Fadenstack betreiben: den Server, die GPU-Maschinen und die Cluster, die sie
bilden. Er beschreibt, wie Sie den Server installieren, aktualisieren, absichern und sichern, Maschinen anbinden
und alles am Laufen halten.

## Was Sie betreiben {#what-you-run}

| Teil | Was es ist | Wie Sie es verwalten |
|---|---|---|
| **Der Server** | Eine Linux-Maschine, auf der Fadenstack als eine Reihe von Docker-Containern läuft. Er braucht keine GPU. | Sie installieren und aktualisieren ihn mit dem Kommandozeilenwerkzeug `faden`. |
| **GPU-Maschinen** | Linux-Maschinen mit NVIDIA-GPUs, auf denen die Modelle laufen. | Sie fügen eine Maschine hinzu, indem Sie auf ihr einen Befehl ausführen und sie in der Konsole genehmigen. |
| **Cluster** | Ein Cluster fasst eine oder mehrere Maschinen zusammen, damit sie gemeinsam Modelle bereitstellen können. | Cluster legen Sie in der Konsole an und verwalten sie dort; die Modelle selbst stellt ein Administrator bereit (siehe [Bereitstellungen](../administer/deployments.md)). |

Ein Cluster läuft auf seinen Maschinen, nicht auf dem Server. Seine Modelle laufen weiter, während der Server neu
startet oder aktualisiert wird; nur Anfragen über das Gateway warten, bis das Gateway wieder da ist.

??? info "Mehr zu den Teilen"
    - **Die Container des Servers:** die Konsole, die API, das OpenAI-kompatible Gateway (`/v1`), die Datenbank
      sowie die Speicher für Logs und Metriken.
    - **Der Node-Agent:** Auf jeder GPU-Maschine läuft ein kleines Programm, der Node-Agent (`faden-agent`), der
      von sich aus die Verbindung zum Server aufbaut und ausführt, was die Konsole von ihm verlangt.
    - **Das Runtime-Image:** Die Maschinen führen die Modelle in einem Runtime-Image aus: einem Container mit Ray,
      das die Arbeit auf die Maschinen verteilt, und vLLM, der Engine, die ein Modell ausführt.

## Die Schritte der Reihe nach {#the-order-of-work}

1. Prüfen Sie die [Systemanforderungen](requirements.md) für den Server und die Maschinen.
2. [Installieren Sie den Server](installing.md) und melden Sie sich an.
3. Entscheiden Sie sich für ein Zertifikat: [HTTPS und Vertrauen](https.md). Eine neue Installation liefert von
   Anfang an HTTPS aus.
4. Öffnen Sie die [Ports](network.md) zwischen den Benutzern, dem Server und den Maschinen.
5. [Fügen Sie die GPU-Maschinen hinzu](machines.md).
6. [Erstellen Sie aus ihnen einen Cluster](clusters.md).
7. Übergeben Sie an den Administrator, der Modelle bereitstellt und Personen hinzufügt (siehe
   [Verwalten](../administer/index.md)).
8. Legen Sie [Sicherungen](backups.md) an, und [aktualisieren](upgrading.md) Sie, wenn ein neues Release
   erscheint. Läuft die Installation noch mit veröffentlichten Schlüsseln oder ist die `.env` womöglich abgeflossen,
   [erneuern Sie die Schlüssel](rotating-keys.md).

## Wenn etwas schiefgeht {#when-something-goes-wrong}

- [Fehler und Logs](failures.md): was sich von selbst erholt, was Sie währenddessen sehen und wo die Logs liegen.
- [Fehlerbehebung](troubleshooting.md): häufige Probleme, ihre Ursache und was zu tun ist.

## Außerdem hier {#also-here}

- [Vorhandene vLLM-Server](existing-vllm.md): einen vLLM, den Sie bereits betreiben, hinter das Gateway stellen.
- [Cluster übernehmen](taking-over.md): Ein neu aufgesetzter Server übernimmt die Cluster, die seine Maschinen
  noch ausführen.
- [Container](containers.md): die Docker-Container auf dem Server, wie die Konsole sie zeigt.
- [Komponenten von Drittanbietern](third-party.md): woraus der Server und die Runtime-Images gebaut sind.
