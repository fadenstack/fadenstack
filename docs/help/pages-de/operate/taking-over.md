# Cluster übernehmen {#taking-over-clusters}

Wird der Server ohne seine Datenbank neu aufgesetzt oder aus einer Sicherung wiederhergestellt, die älter ist als
seine Cluster, betreiben die GPU-Maschinen weiter die Cluster und Modelle, die der alte Server gestartet hat. Der neue
Server kann einen solchen Cluster im laufenden Betrieb übernehmen: seine Maschinen und jedes Modell, das er
bereitstellt. Kein Modell startet neu, weder während der Übernahme noch danach.

??? info "Warum den Cluster nicht neu anlegen?"
    Den Cluster stattdessen neu anzulegen würde jedes Modell neu starten, und solange die alten Kopien ihre GPUs noch
    belegen, passen die neuen oft nicht.

## Bevor Sie beginnen {#before-you-start}

- **Die Maschinen sind auf diesem Server genehmigt.** Ihre Agenten haben noch Zugangsdaten für den alten Server.
  Führen Sie auf jeder Maschine die Installationszeile aus **Maschinen → Maschine hinzufügen** dieses Servers aus und
  genehmigen Sie die Anfrage. Das erneute Installieren des Agents berührt weder den laufenden Cluster noch seine
  Modelle.
- **Auf den Maschinen läuft ein aktueller Agent.** Das Ausführen der Installationszeile erledigt das.

## Einen Cluster übernehmen {#take-over-a-cluster}

1. Öffnen Sie **Cluster** und suchen Sie die Karte **Läuft auf Ihren Maschinen, wird hier nicht verwaltet**.
2. Wählen Sie beim Cluster **Übernehmen…**.
3. Geben Sie einen **Clustername** ein.
4. Geben Sie für jedes Modell den Namen ein, unter dem Clients es am Gateway anfragen
   (*Modell*: **Name am Gateway**). Vorgeschlagen wird der eigene Name des Modells in Kleinbuchstaben.
5. Wählen Sie **Übernehmen**.

Das dauert einige Sekunden. Danach öffnet sich der Cluster mit seinen laufenden Modellen, und das Gateway bietet
jedes Modell unter dem Namen an, den Sie vergeben haben.

Bevor etwas erfasst wird, wird der Cluster gebeten zu bestätigen, dass das, was dieser Server für jedes Modell
bereitstellen würde, genau dem entspricht, was läuft. Weicht etwas ab, wird nichts erfasst, und der Dialog zeigt den
Unterschied.

??? info "Mehr zur Karte und zu den Namen"
    Die Karte listet jeden Cluster auf, den eine Maschine betreibt und den dieser Server nicht verwaltet: seine
    Maschinen und jedes Modell mit seinen Kopien und den GPUs pro Kopie.

    Der alte Server hat die Namen am Gateway in seiner eigenen Datenbank gespeichert, nicht auf den Maschinen,
    deshalb müssen sie erneut eingegeben werden.

## Was übernommen wird und was nicht {#what-is-carried-over-and-what-is-not}

| Was | Übernommen |
|---|---|
| Die Maschinen des Clusters, das Netzwerk, in dem er läuft, sein Token | Ja |
| Jedes Modell, das Fadenstack auf ihm bereitgestellt hat, mit seinen Einstellungen und Kopien | Ja |
| **Die Namen**, die in der Konsole angezeigt und am Gateway verwendet werden | Nein. Sie geben sie bei der Übernahme erneut ein |
| **Verlauf**: Logs, Metriken und vergangene Anfragen | Nein. Sie bleiben beim alten Server |
| **Anwendungen, die Fadenstack nicht bereitgestellt hat** | Nein. Sie laufen weiter und werden aufgeführt („Läuft ebenfalls, nicht von Fadenstack bereitgestellt und bleibt unverändert“), aber nicht verwaltet |

## Wenn sich ein Cluster nicht übernehmen lässt {#when-a-cluster-cannot-be-taken-over}

Die Karte nennt den Grund, und **Übernehmen…** bleibt deaktiviert:

| Meldung | Was zu tun ist |
|---|---|
| *Maschine* is in the cluster but not in this fleet | Führen Sie die Installationszeile dieses Servers auf dieser Maschine aus und genehmigen Sie sie |
| … already belong to a cluster this server manages | Diese Maschine gehört hier zu einem Cluster. Entfernen Sie sie zuerst aus diesem Cluster |
| This server already has the deployment of … | Das Modell wird hier bereits verwaltet; es gibt nichts zu übernehmen |
| *Maschine* führt die Cluster-Runtime aus, konnte aber nicht angeben, was sie bereitstellt | Ihr Agent ist zu alt oder hat nicht geantwortet: Führen Sie die Installationszeile erneut auf ihr aus |

Findet die Prüfung im letzten Schritt einen Unterschied, zeigt der Dialog die Einstellung und beide Werte, z. B.
`llm_configs.0.engine_kwargs.max_model_len: running 8192, would deploy 32768`. Es wurde nichts erfasst. Das Modell
wurde von einer Version von Fadenstack bereitgestellt, die es anders eingerichtet hat. Stellen Sie es entweder von
diesem Server aus neu bereit (es startet neu), oder lassen Sie es unverwaltet weiterlaufen.

## Verwandte Themen {#related}

- [Sicherungen](backups.md): einen Server wiederherstellen, und warum eine Sicherung, die älter ist als ein Cluster,
  ihn nicht kennt.
- [Cluster](clusters.md#models-running-without-a-deployment): ein Modell, das weiterläuft, nachdem seine
  Bereitstellung gelöscht wurde, auf einem Cluster, den dieser Server verwaltet.
