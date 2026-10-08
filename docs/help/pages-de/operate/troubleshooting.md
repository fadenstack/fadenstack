# Fehlerbehebung {#troubleshooting}

Probleme, auf die Sie im Betrieb stoßen, mit ihrer Ursache und dem, was zu tun ist. Was Fadenstack selbst repariert
und wo die Logs liegen, steht unter [Fehler und Logs](failures.md).

## Der Server {#the-server}

| Sie sehen | Was Sie tun |
|---|---|
| **Die Konsole antwortet nicht.** | Ein Dienst ist ausgefallen, oder der Proxy kann auf seinem Port nicht starten. Führen Sie `faden status` aus. Bei einem Dienst, der nicht läuft oder fehlerhaft ist, lesen Sie sein Log (`faden logs faden-backend`, `faden logs nginx`). `faden doctor` zeigt, ob etwas anderes Port 80 oder 443 belegt; ändern Sie die Ports bei Bedarf mit `faden ports --http 8080 --https 8443`. |
| **Jede Seite zeigt „Fadenstack wird aktualisiert“ oder „Fadenstack wird gewartet“.** | Der Wartungsmodus ist eingeschaltet, durch ein Upgrade oder von Hand. `faden maintenance status` nennt den Grund und seit wann. `faden maintenance off` hebt ihn auf. |
| **Dienste starten immer wieder neu: Postgres, der Log-Speicher, der Trace-Speicher.** | Meist eine volle Festplatte. Prüfen Sie das mit `df -h`. Entfernen Sie Images, die kein Container verwendet (`docker image prune`), verschieben Sie alte Sicherungen vom Server und führen Sie dann `faden up` aus. Das Dashboard warnt früher: **Wenig Speicherplatz**. |
| **Das Passwort des Administrators ist verloren.** | `faden admin reset-password --email alex@example.com` setzt ein neues und zeigt es an. Lassen Sie `--password` weg, um eines erzeugen zu lassen. |
| **Der Browser warnt vor dem Zertifikat.** | Der Server verwendet ein Zertifikat, das er selbst erstellt hat, und dieser Computer vertraut dessen Zertifizierungsstelle noch nicht. Importieren Sie `~/fadenstack/tls/ca.crt` als vertrauenswürdige Zertifizierungsstelle (siehe [HTTPS und Vertrauen](https.md#trusting-a-certificate-made-on-the-server)). |
| **Die Browser-Konsole zeigt `Cannot read properties of undefined (reading 'keys')`.** | Grafana im Dashboard-Bereich auf der Startseite der Konsole braucht eine sichere Seite. Über einfaches HTTP protokolliert es diesen Fehler; das Dashboard funktioniert trotzdem. Stellen Sie die Konsole über HTTPS bereit. |
| **`faden upgrade` bricht ab, bevor es etwas ändert.** | Lesen Sie seine Meldung; [Upgrade](upgrading.md#when-it-does-not-work) listet die Meldungen auf. |

## Eine Maschine hinzufügen {#adding-a-machine}

| Sie sehen | Was Sie tun |
|---|---|
| **Das Installationsprogramm fragt nach einem `sudo`-Passwort, das Sie nicht haben.** | Führen Sie die Zeile ohne `sudo` aus. Der Agent wird in Ihrem Home-Verzeichnis installiert und läuft als Benutzerdienst. Ihr Benutzer muss trotzdem Docker oder Podman verwenden dürfen. |
| **Die Maschine hat einen Code angezeigt, aber in der Konsole erscheint nichts.** | Die Anfrage ist abgelaufen, oder die Gruppe **KI-Infrastruktur** in der Navigation ist zugeklappt (sie zeigt dann einen Punkt statt der Anzahl). Öffnen Sie **Maschinen**. Wartet dort nichts, führen Sie die Zeile erneut aus, um einen neuen Code zu erhalten. |
| **Der Agent stoppt, wenn Sie sich abmelden.** | Der Agent läuft als Benutzerdienst, und *Linger* ist für Ihren Benutzer nicht eingeschaltet. Ein Administrator der Maschine führt einmalig `sudo loginctl enable-linger <user>` aus. |
| **Die Maschine ist beigetreten, zeigt aber keine GPU.** | Der Dienst findet den NVIDIA-Treiber nicht, obwohl Ihre Shell ihn findet. Prüfen Sie, ob `nvidia-smi` in einem Standardpfad liegt und ob der Dienst den Agenten ausführt, den Sie installiert haben: `sudo systemctl show faden-agent -p ExecStart` (oder `systemctl --user show faden-agent -p ExecStart`). |
| **Das Installationsprogramm bricht mit `the download does not match the expected digest` ab.** | Die Kopie des Agenten auf dem Server in `~/fadenstack/agent-binaries/` stammt aus einem anderen Release. Ersetzen Sie sie durch die Builds des aktuellen Release. |
| **Eine Maschine, die Sie gestoppt oder entfernt haben, wird weiterhin als in Ordnung angezeigt.** | Auf ihr läuft irgendwo noch ein Agent, z. B. einer, der von Hand mit `faden-agent run` gestartet wurde und kein Dienst ist. Eine Maschine wird als offline markiert, sobald ihr Agent die Verbindung trennt. Führen Sie auf der Maschine `pgrep -af faden-agent` aus. Kommt etwas zurück, beendet ihn `sudo faden-agent stop` (oder `faden-agent stop`). |
| **Die Logs einer Maschine erscheinen nicht unter Logs.** | Ihr Agent ist zu alt, um seine Logs über den Server zu senden. Führen Sie die Installationszeile auf der Maschine erneut aus, um den Agenten zu aktualisieren. |
| **Nach `faden tls off` bleiben die Maschinen offline.** | Ein Agent, der auf HTTPS umgestellt hat, stellt nie von selbst zurück. Führen Sie auf jeder Maschine `faden-agent configure --set BACKEND_URL=http://ai.example.internal` aus und starten Sie dann den Agenten neu (`sudo systemctl restart faden-agent` oder `systemctl --user restart faden-agent`). |

## Cluster {#clusters}

| Sie sehen | Was Sie tun |
|---|---|
| **Der Cluster startet nicht und meldet `Address already in use`.** | Etwas auf einer Maschine belegt einen der Ports des Clusters, oft ein anderes Redis oder ein anderes Ray. Geben Sie den Port frei (siehe [Netzwerk und Ports](network.md#ports-a-gpu-machine-must-have-free)) und wählen Sie dann auf der Seite des Clusters **Erneut versuchen**. |
| **Der Cluster meldet, dass er darauf wartet, dass sich die Ursache ändert.** | Ein Fehler, der bei jedem Versuch gleich ausfällt, meist ein Runtime-Image auf dem Server, das nicht der Build ist, den der Cluster braucht. Der Cluster versucht es dann nicht mehr in einer Schleife, sondern prüft etwa einmal pro Stunde erneut. `faden runtime-images --check` zeigt, was der Server hat; `faden runtime-images` holt, was fehlt. Wählen Sie dann **Erneut versuchen**. |
| **Das Kopieren des Runtime-Images auf eine Maschine wurde unterbrochen.** | Nichts. Es geht dort weiter, wo es aufgehört hat, vom Server oder von einer Maschine des Clusters, die das Image hat, solange die Maschine Platz für eine Kopie des Images auf der Festplatte hat; sonst beginnt es von vorn. |
| **Der Cluster meldet, dass er sich selbst neu startet.** | Ray ist auf der Head-Maschine gestoppt, oder eine Maschine ist ausgefallen. Fadenstack repariert das. Nichts zu tun, es sei denn, nach drei Versuchen steht dort **Fehlgeschlagen**. Siehe [Fehler und Logs](failures.md). |
| **Ein Cluster lässt sich nicht löschen, weil seine Maschinen ausgeschaltet sind.** | Wählen Sie trotzdem **Löschen**. Ist keine Maschine erreichbar, gibt es nichts zu stoppen; die Seite sagt das und löscht den Cluster. |
| **Eine Maschine lässt sich keinem Cluster hinzufügen.** | **Maschinen hinzufügen** zeigt den Grund unter jeder Maschine; [Cluster](clusters.md#add-machines-to-a-running-cluster) erklärt, was die Gründe bedeuten. |
| **Das Leeren wird nicht fertig.** | Der Ersatz einer Kopie ist auf der anderen Maschine nicht gestartet. Nach 30 Minuten meldet das Leeren das. Öffnen Sie die Bereitstellung und lesen Sie ihre Fehler, oder wählen Sie **Leeren beenden**. |

## Modelle {#models}

| Sie sehen | Was Sie tun |
|---|---|
| **Eine Bereitstellung bleibt bei „Modell wird kopiert“ oder „Downloading … to the Fadenstack server“ stehen.** | Der Server lädt das Modell noch herunter, oder der Download ist fehlgeschlagen. Verfolgen oder wiederholen Sie den Download unter **Modell-Marktplatz → Auf diesem Server**. Ein zugangsbeschränktes Modell braucht ein Hugging-Face-Token von einem Konto, das seine Lizenz akzeptiert hat (siehe [Modelle](../administer/models.md)). |
| **Eine Bereitstellung erreicht „Wird gestartet“ und schlägt dann fehl.** | Die Engine konnte das Modell nicht starten; die Meldung nennt ihren eigenen Grund. Meist passt das Modell nicht in den Speicher der GPU, oder es braucht ein Zahlenformat, das die Karte nicht unterstützt. Ändern Sie die Engine-Einstellungen oder den Anteil je Beschleuniger, wie unter [Bereitstellungen](../administer/deployments.md) beschrieben. |
| **Eine Bereitstellung läuft mit weniger Kopien als angefordert.** | „cannot start: each copy needs …“ bedeutet, dass der Cluster keinen Platz für weitere hat; alles andere ist der Grund der Engine für die Kopie, die sie nicht starten konnte. Skalieren Sie herunter, oder [fügen Sie dem Cluster eine Maschine hinzu](clusters.md#add-machines-to-a-running-cluster). |
| **Antworten schlagen fehl mit „The model did not start its reply within … s“.** | Das Modell ist ausgelastet; seine Warteschlange ist voll, und eine neue Anfrage hat zu lange gewartet. Geben Sie dem Modell mehr Platz: mehr Kopien oder mehr GPU-Speicher pro Kopie (siehe [Bereitstellungen](../administer/deployments.md)). |
| **Ein vLLM, das bereits auf einer Maschine läuft, lässt sich nicht routen.** | [Vorhandene vLLM-Server](existing-vllm.md#what-cannot-be-routed) nennt die Gründe. |
