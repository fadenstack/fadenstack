# GPU-Maschinen {#gpu-machines}

Eine GPU-Maschine tritt Fadenstack bei, indem Sie einen Befehl auf ihr ausführen und sie in der Konsole genehmigen.
Danach verwalten Sie sie über die Konsole: ihre Hardware, ihre Logs, Wartung, Leeren und die Cluster, zu denen sie
gehört.

Die Konsole nennt sie **Maschinen**. Der Agent, die API und manche Meldungen sagen noch *node* bzw. *Knoten*; gemeint
ist dasselbe.

## Bevor Sie beginnen {#before-you-start}

- Sie sind in der Konsole als Administrator angemeldet.
- Die Maschine erfüllt die [Systemanforderungen](requirements.md#gpu-machines): Linux, x86_64 oder aarch64, eine
  NVIDIA-GPU, Docker oder Podman.
- `nvidia-smi` gibt auf der Maschine Ihre Karte aus. Eine Maschine, die ihre GPU nicht sieht, tritt trotzdem bei,
  kann aber keinem Cluster hinzugefügt werden.
- Die Maschine erreicht den Server unter einer Adresse, die Sie kennen, z. B. `https://ai.example.internal`.

## Eine Maschine hinzufügen {#add-a-machine}

1. Öffnen Sie in der Konsole **KI-Infrastruktur → Maschinen** und wählen Sie **Maschine hinzufügen**.

    ![Der Bereich Maschine hinzufügen: die zu kopierende Installationszeile, die Liste der Maschinen, die auf Genehmigung warten, und Genehmigungsschritt überspringen](../images/operate/add-machine-panel.png)

2. Wenn der Bereich angibt, dass der Server mehr als eine Adresse hat, wählen Sie die, die die Maschine erreichen
   kann. Jede Adresse ist mit ihrer Netzwerkschnittstelle aufgeführt; der Befehl ändert sich mit Ihrer Auswahl.
3. Wählen Sie **Befehl kopieren**.
4. Führen Sie den Befehl in einem Terminal auf der Maschine aus. Er installiert den Agent, zeigt einen kurzen Code an
   und wartet.
5. Zurück in der Konsole erscheint die wartende Maschine im Bereich unter **Wartet auf Genehmigung**, und die Seite
   **Maschinen** zeigt an, wie viele Maschinen warten, mit **Prüfen**.
6. Prüfen Sie, ob der Code mit dem auf der Maschine übereinstimmt, und wählen Sie **Genehmigen**. **Nicht diese**
   lehnt sie ab.

Die Maschine wird als **healthy** aufgeführt, und ihre Seite zeigt ihre GPUs. Als Nächstes: Nehmen Sie sie in einen
[Cluster](clusters.md) auf.

![Die Seite Maschinen mit zwei Maschinen im Zustand healthy, gpu-01 und gpu-02, und der Schaltfläche Maschine hinzufügen](../images/operate/machines.png)

### Die Installationszeile {#the-install-line}

```bash
curl -fsSLO https://ai.example.internal/api/install/faden-agent.sh && sudo sh faden-agent.sh --join https://ai.example.internal
```

Das Installationsprogramm wird von Ihrem Server erzeugt: Es kennt bereits die Adresse des Servers und die
Agent-Version, die der Server erwartet. Es wird auf die Festplatte geschrieben, bevor es läuft, sodass Sie es vorher
lesen können. Es lädt den Agent herunter, installiert ihn und stellt eine Beitrittsanfrage. Wenn der Server eine
eigene Kopie der Agent-Builds bereithält, wird der Download gegen sie geprüft (siehe
[Maschinen ohne Internetzugang](#machines-without-internet-access)).

Solange der Server ein selbst erstelltes Zertifikat verwendet, ist die Zeile in der Konsole länger: Sie ruft zuerst
die Zertifizierungsstelle des Servers ab und prüft sie gegen einen Fingerabdruck, der in der Zeile steht, sodass
nichts als vertrauenswürdig gilt, was nicht von Ihrem Server stammt (siehe
[HTTPS und Vertrauen](https.md#the-machines)).

### Ohne sudo {#without-sudo}

Lassen Sie `sudo` in der Zeile weg. Unter Ihrem eigenen Benutzer ausgeführt, legt das Installationsprogramm den
Agent in `~/.local/bin` ab und betreibt ihn als systemd-Benutzerdienst. Nichts fragt nach einem Passwort. Ihr
Benutzer muss aber Docker (oder Podman) verwenden dürfen.

Ein Benutzerdienst endet, wenn Sie sich abmelden, es sei denn, *linger* ist für Ihren Benutzer eingeschaltet. Das
Installationsprogramm schaltet es ein; wo die Maschine das nicht zulässt, meldet es das und nennt den Befehl, den ein
Administrator einmal ausführt:

```bash
sudo loginctl enable-linger <user>
```

Wenn Ihr Benutzer `sudo` ohne Passwort verwenden kann, Sie aber trotzdem einen Benutzerdienst möchten, ergänzen Sie
die Zeile um `--user`.

### Ohne jede Maschine zu genehmigen {#without-approving-each-machine}

Für Ansible, cloud-init, Image-Builds oder viele Maschinen auf einmal:

1. Wählen Sie in **Maschine hinzufügen** die Option **Genehmigungsschritt überspringen**.
2. Schreiben Sie bei Bedarf eine **Notiz** (für welche Maschine oder welches Rack), und wählen Sie
   **Token erstellen**.
3. Kopieren Sie den Befehl. Er enthält ein einmal verwendbares Token, das abläuft; es wird nur einmal angezeigt.

Eine Maschine, die ihn ausführt, tritt von selbst bei; es gibt nichts zu genehmigen.

### Maschinen ohne Internetzugang {#machines-without-internet-access}

Das Installationsprogramm versucht zuerst das veröffentlichte Release des Projekts und greift andernfalls auf die
eigene Kopie des Agents auf dem Server zurück. Der Server hat eine solche Kopie, wenn Sie die Builds in
`~/fadenstack/agent-binaries/` ablegen: `faden-agent-linux-x86_64` und `faden-agent-linux-aarch64` mit ihren
`.sha256`-Dateien, von der Release-Seite des Projekts. Ersetzen Sie sie nach jedem Upgrade des Servers.

Mit dieser Kopie wird jeder Download, aus welcher Quelle auch immer, gegen sie geprüft. Ohne sie meldet das
Installationsprogramm `this backend published no digest, so nothing was verified`, und eine Maschine ohne
Internetzugang bricht mit `could not download the agent` ab.

## Was die Seite einer Maschine zeigt {#what-a-machines-page-shows}

Öffnen Sie **Maschinen** und wählen Sie eine Maschine aus.

![Die Seite einer Maschine: Status und Agent-Version, die Aktionen Wartung, Leeren und weitere, ihre Hardware und die darauf gefundenen vLLM-Container](../images/operate/machine.png)

- **Status**: **healthy**, **degraded**, **unhealthy** oder **offline**, mit Chips für **Wartung**,
  **Wird geleert** und **Agent *Version* verfügbar**, wenn der Server einen neueren Agent erwartet. Die Version des
  Agents selbst steht unter dem Namen. **Live** aktualisiert die Seite von selbst.
- **Hardware**: Auslastung von **CPU**, **Speicher**, **Festplatte**, **GPU** und **Netzwerk**, die
  **GPU-Geräte** und **System** (Betriebssystem, Architektur, Docker, Netzwerkschnittstellen).
- **Systemprotokolle**: die letzten 15 Minuten der eigenen Logs der Maschine. **In Logs öffnen** öffnet die
  vollständige Seite **Logs** für diese Maschine.
- **Bereitstellungen auf dieser Maschine**: was ihre Cluster bereitstellen.
- **vLLM auf dieser Maschine**: vLLM-Container, die Fadenstack nicht gestartet hat (siehe
  [Vorhandene vLLM-Server](existing-vllm.md)).
- **Befehle** und **Zeitverlauf**: worum der Server die Maschine gebeten hat und was daraufhin geschah.
- **Rohinventar & Auslastung**: alles, was der Agent meldet, als Daten.

Und ihre Aktionen:

| Aktion | Was sie tut |
|---|---|
| **Wartung** / **Wartung beenden** | Markiert die Maschine zur Wartung (siehe unten) |
| **Leeren** / **Leeren beenden** | Zieht ihre Modellkopien auf die anderen Maschinen des Clusters um (siehe [Cluster](clusters.md#drain-a-machine)) |
| **Inventar aktualisieren** | Der Agent liest die Hardware erneut ein |
| **Diagnose** | Sammelt die Diagnoselogs des Agents und den Systemzustand |
| **Systemaktualisierungen** | Sucht nach Paketen des Betriebssystems und Firmware-Updates der Maschine, installiert sie und kann die Maschine neu starten |
| Enrollment token | Ein einmal verwendbares Token für diese Maschine |

**Systemaktualisierungen** führt den Paketmanager mit `sudo` aus und funktioniert daher nur, wo der Benutzer des
Agents das ohne Passwort darf.

## Wartung und Leeren {#maintenance-and-draining}

**Wartung** markiert eine Maschine zur Wartung: Es wird nichts Neues auf ihr platziert, sie kann keinem Cluster
hinzugefügt werden, und was bereits auf ihr läuft, läuft weiter. Verwenden Sie sie, während Sie an einer Maschine
arbeiten.

**Leeren** räumt eine Maschine: Ihre Modellkopien ziehen auf die anderen Maschinen des Clusters um, jede beantwortet
weiter Anfragen, bis ihr Ersatz läuft, und keine neuen Kopien landen auf ihr. Sie sehen vorher, welche Kopien
umziehen können und welche nicht. Leeren Sie eine Maschine, bevor Sie sie neu starten, ihre Hardware ändern oder sie
aus ihrem Cluster nehmen. Details: [Eine Maschine leeren](clusters.md#drain-a-machine).

Beides gibt es auch in der Liste **Maschinen**, als Schaltflächen **Wartung aktivieren** und **Leeren** in der Zeile
der Maschine.

## Den Agent aktualisieren {#update-the-agent}

Wenn eine Maschine **Agent *Version* verfügbar** zeigt, führen Sie dieselbe Installationszeile erneut auf ihr aus,
auf dieselbe Weise wie beim ersten Mal (mit oder ohne `sudo`). Der Agent startet mit dem neuen Build neu; nichts muss
genehmigt werden, und die Modelle laufen weiter. Siehe [Upgrade](upgrading.md#update-the-machines-agents).

## Auf der Maschine selbst {#on-the-machine-itself}

`faden-agent` ohne Argumente bietet ein Menü an. Die Befehle:

| Befehl | Was er tut |
|---|---|
| `faden-agent status` | Ob der Dienst läuft |
| `faden-agent show` | Die verwendete Konfiguration, die Adresse des Servers und ob der Agent zu HTTPS gewechselt hat |
| `faden-agent configure --set KEY=VALUE` | Ändert eine Einstellung, z. B. `BACKEND_URL` oder `ADVERTISE_HOST` |
| `faden-agent stop` | Stoppt den Dienst und entfernt ihn (mit `sudo` bei einem Systemdienst) |
| `faden-agent run` | Läuft im Vordergrund, damit Sie sehen, was schiefgeht |
| `faden-agent scan` | Listet die Modelle auf, die bereits im Modellspeicher der Maschine liegen |

Das eigene Log des Agents steht im Systemjournal: `journalctl -u faden-agent` bei einem Systemdienst,
`journalctl --user -u faden-agent` bei einem Benutzerdienst. Starten Sie ihn nach dem Ändern einer Einstellung neu:
`sudo systemctl restart faden-agent` oder `systemctl --user restart faden-agent`.

### Eine Maschine mit mehreren Netzwerken {#a-machine-with-several-networks}

Die Adresse, mit der eine Maschine aufgeführt ist, ist die, die sie dem Server und den anderen Maschinen zur
Verwendung mitteilt. Nennt sie eine Adresse, die diese nicht erreichen können (ein VPN, eine Container-Bridge),
setzen Sie die richtige und starten Sie den Agent neu:

```bash
faden-agent configure --set ADVERTISE_HOST=192.0.2.21
```

Das Netzwerk, in dem ein Cluster läuft, wählen Sie separat beim Anlegen des Clusters.

## Eine Maschine entfernen {#remove-a-machine}

1. Wenn die Maschine zu einem Cluster gehört, nehmen Sie sie zuerst heraus: Wählen Sie auf der Seite des Clusters
   die Maschine und **Aus Cluster entfernen** (siehe [Cluster](clusters.md#remove-a-machine-from-a-cluster)).
2. Stoppen Sie auf der Maschine den Agent: `sudo faden-agent stop` (oder `faden-agent stop` bei einem
   Benutzerdienst).
3. Prüfen Sie, dass nichts mehr läuft: `pgrep -af faden-agent` gibt nichts aus.
4. Wählen Sie in **Maschinen** in ihrer Zeile **Maschine löschen**, geben Sie zur Bestätigung ihren Namen ein und
   wählen Sie **Löschen**. Wurden Runtimes oder Provider auf ihr eingerichtet, können Sie diese mit löschen.

Das Löschen einer Maschine widerruft ihre Mitgliedschaft. Um sie zurückzuholen, führen Sie die Installationszeile
erneut auf ihr aus; sie stellt dann wie beim ersten Mal eine Beitrittsanfrage.

!!! warning
    Löschen Sie eine Maschine erst, wenn ihr Agent gestoppt ist. Eine Maschine, die gelöscht wird, während ihr Agent
    noch läuft, fragt immer wieder nach dem Beitritt.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**Das Installationsprogramm fragt nach einem `sudo`-Passwort, das Sie nicht haben.** Lassen Sie `sudo` in der Zeile
weg (siehe [Ohne sudo](#without-sudo)).

**`ERROR: this backend publishes no agent build for linux-…`.** Der Server hat keinen Agent-Build für diese Art von
Maschine, und das Release war nicht erreichbar. Legen Sie die Builds in `~/fadenstack/agent-binaries/` ab.

**`ERROR: the download does not match the expected digest.`** Der Download ist nicht der Build, den der Server
erwartet, meist weil die Kopie in `~/fadenstack/agent-binaries/` aus einem älteren Release stammt. Ersetzen Sie sie
durch die Builds des aktuellen Releases.

**Die Maschine erscheint nie zur Genehmigung.** Eine Anfrage läuft ab, wenn sie niemand rechtzeitig genehmigt; führen
Sie die Zeile erneut aus, um einen neuen Code zu erhalten. Kann die Maschine den Server überhaupt nicht erreichen,
meldet das Installationsprogramm `could not reach` mit der Adresse: Wählen Sie in **Maschine hinzufügen** eine andere
Adresse.

**Die Maschine ist beigetreten, zeigt aber keine GPU.** Der Treiber funktioniert in Ihrer Shell, aber nicht für den
Dienst. Prüfen Sie, ob `nvidia-smi` in einem Standardpfad liegt und ob der Dienst den Agent ausführt, den Sie
installiert haben: `sudo systemctl show faden-agent -p ExecStart` (oder
`systemctl --user show faden-agent -p ExecStart`).

Mehr: [Fehlerbehebung](troubleshooting.md).
