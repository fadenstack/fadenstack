# Systemanforderungen {#system-requirements}

Was der Server und die GPU-Maschinen brauchen, bevor Sie etwas installieren. Die Größe des Servers richtet sich
nach den Diensten, die er ausführt, und nach den Personen, die sie nutzen; die Größe einer GPU-Maschine nach den
Modellen, die sie ausführt.

## Der Server {#the-server}

| | Minimum | Empfohlen |
|---|---|---|
| Betriebssystem | Linux, x86_64 | |
| CPU-Kerne | 4 | 8 |
| Arbeitsspeicher, Kerndienste | 6 GB | 8 GB |
| Arbeitsspeicher, mit mehrsprachigem PII, MCP und Skills | 12 GB | 16 GB |
| Speicherplatz | 60 GB | 100 GB, zuzüglich der Größe der Modelle, die Sie bereitstellen wollen |
| GPU | keine | keine |

| Der Server braucht außerdem | |
|---|---|
| **Docker Engine 24 oder neuer mit Compose v2** | Der Benutzer, der `faden` ausführt, muss Docker verwenden dürfen (also in der Gruppe `docker` sein). |
| **Python 3.12 oder neuer** | Mit `pipx` oder `uv`, um das Kommandozeilenwerkzeug `faden` zu installieren. |
| **Ports 80 und 443 frei** | Auf dem Server. Andere Ports lassen sich wählen; siehe [Netzwerk und Ports](network.md). |
| **Internetzugang während Installation und Upgrade** | Um die Images der Dienste und die Runtime-Images herunterzuladen. Siehe [Was einen Internetzugang braucht](#what-needs-the-internet). |

Rechnen Sie Arbeitsspeicher für alles hinzu, was sonst noch auf dem Server läuft. Der Server braucht keine GPU:
Die Modelle laufen auf den Maschinen, die Sie hinzufügen. Einen Server mit NVIDIA-GPU können Sie zusätzlich als
Maschine hinzufügen.

`faden doctor` prüft einen Host vor der Installation: Arbeitsspeicher, freien Speicherplatz, Docker, Compose und
ob die Ports frei sind, die die Installation nach außen öffnet.

### Was womit wächst {#what-grows-with-what}

| Wenn Sie hinzufügen | Was auf dem Server wächst |
|---|---|
| Personen, die gleichzeitig chatten | Zuerst die CPU-Last des Gateways und der Konsolen-API, dann die Datenbankverbindungen |
| Das PII-Modul | Zusätzlicher Arbeitsspeicher für das gewählte Modell und mehr CPU pro Chat-Runde; mindestens 4 GB für den mehrsprachigen PII-Container zusätzlich zu den anderen Diensten einplanen |
| MCP-Tools, Skills | Einige hundert MB Arbeitsspeicher |
| Modelle | Speicherplatz: Jedes Modell wird auf den Server heruntergeladen und von dort auf die Maschinen kopiert |
| GPU-Maschinen | Ein Runtime-Image pro Maschinenart auf der Festplatte; bei CPU und Arbeitsspeicher nichts Messbares |
| Tracing | Speicherplatz für den Trace-Speicher |

Um die Dienste nach einer Änderung der Hardware neu zu bemessen, führen Sie `faden tune` und danach `faden up` aus.

PII kann eine kompatible lokale NVIDIA-GPU oder ohne GPU die CPU verwenden. Die CPU-Verarbeitung ist bei
gleichzeitigen Anfragen und langen Dokumenten langsamer. `faden doctor` zeigt Kompatibilität und CPU-Kapazität.
Siehe [Lokale PII-Verarbeitung auf CPU oder GPU](installing.md#local-pii-on-cpu-or-gpu).

??? info "Mehr zur Bemessung der Dienste"
    `faden deploy` und `faden upgrade` bemessen die Dienste für den Host, auf dem sie laufen: wie viele
    Worker-Prozesse jeder Dienst bekommt und wie viele Datenbankverbindungen Postgres annimmt.

### Speicherplatz {#disk}

| Was | Größe | Nötig |
|---|---|---|
| Images der Dienste | etwa 11 GB | immer |
| Runtime-Image, x86_64-Maschinen | etwa 15 GB | wenn Sie x86_64-GPU-Maschinen hinzufügen |
| Runtime-Image, Maschinen vom Typ NVIDIA DGX Spark | etwa 12 GB | wenn Sie DGX-Spark-Maschinen hinzufügen |
| Modellspeicher | die Modelle, die Sie bereitstellen | immer |
| Datenbank | wächst mit dem Chatverlauf | immer |
| Trace-Speicher | wächst mit den Traces | wenn Tracing eingeschaltet ist |

Der Server hält für jede Maschinenart, die Sie haben, ein Runtime-Image vor, weil die Maschinen es vom Server
herunterladen, nicht aus dem Internet. Wenn Sie nur x86_64-Maschinen haben, installieren Sie mit
`faden deploy --runtime-images x86_64`, um das andere auszulassen.

Sicherungen landen standardmäßig auf derselben Festplatte; lassen Sie Platz dafür, oder legen Sie sie woanders
ab (siehe [Sicherungen](backups.md)).

## GPU-Maschinen {#gpu-machines}

| | |
|---|---|
| Betriebssystem | Linux: x86_64, oder aarch64 nur auf NVIDIA DGX Spark |
| GPU | Nur NVIDIA, mit funktionierendem Treiber: `nvidia-smi` muss Ihre Karte anzeigen |
| Container | Docker oder Podman, laufend und nutzbar für den Benutzer, unter dem der Agent läuft, mit Zugriff auf die GPU (bei Docker: das NVIDIA Container Toolkit) |
| Dienstverwaltung | systemd: Der Agent läuft als Systemdienst oder, wenn er ohne `sudo` installiert wurde, als Benutzerdienst |
| Speicherplatz | Etwa 15 GB für das Runtime-Image plus Platz für die Modelle, die die Maschine ausführt; ein Wechsel der Runtime braucht, solange er läuft, Platz für ein zweites Image |
| Netzwerk | Die Maschine muss den HTTPS-Port des Servers erreichen. Der Server muss den Modell-Port der Maschine erreichen. Maschinen in einem Cluster müssen einander erreichen. Siehe [Netzwerk und Ports](network.md) |

Zwei Maschinenarten werden unterstützt, jede mit eigenem Runtime-Image:

- **x86_64-Maschinen mit NVIDIA-GPUs**, von der Turing-Generation bis Blackwell. Ihr Runtime-Image ist noch
  nicht zertifiziert: Die Karte **Laufzeit** des Clusters zeigt **Nicht zertifiziert**.
- **NVIDIA DGX Spark** (GB10, aarch64).

Andere Maschinen werden nicht unterstützt.

??? info "Was mit anderen Maschinen geschieht"
    Eine Maschine ohne NVIDIA-GPU oder mit einer anderen Prozessorarchitektur kann beitreten, aber keinem Cluster
    zugeordnet werden: Der Cluster-Assistent meldet, dass kein zertifiziertes Runtime-Image auf ihr läuft. Einer
    anderen aarch64-Maschine mit NVIDIA-GPU wird das Image für DGX Spark angeboten, das nicht für sie gemacht ist.

!!! note
    Ein Modell läuft jeweils auf einer GPU-Art. Gemischte Maschinen können sich einen Cluster teilen, aber jede
    Kopie eines Modells landet auf Maschinen einer Art. Maschinen, die gemeinsam ein Modell ausführen sollen,
    brauchen dieselben GPUs.

Der GPU-Speicher entscheidet, welche Modelle passen. Der Modell-Marktplatz zeigt für jedes Modell und jeden
Cluster, ob es auf einen Teil eines Beschleunigers passt, auf einen ganzen, verteilt auf mehrere oder gar nicht
(siehe [Modelle](../administer/models.md)).

## Was einen Internetzugang braucht {#what-needs-the-internet}

| Wer | Erreicht | Wann |
|---|---|---|
| Server | Container-Registries (`ghcr.io`, Docker Hub und die anderen, aus denen die Images stammen) | bei Installation und Upgrade |
| Server | Hugging Face | beim Herunterladen von Modellen |
| Server | Externe Modell-Provider, externe MCP-Server | wenn der Administrator welche anbindet |
| Server | GitHub | um seine eigene Kopie der Agent-Builds zu prüfen, falls er eine vorhält |
| GPU-Maschinen | GitHub (die Releases des Projekts) | beim Installieren oder Aktualisieren des Agenten, sofern der Server die Agent-Builds nicht vorhält |
| GPU-Maschinen | Hugging Face | nur in einem Cluster, der so eingestellt ist, dass seine Maschinen Modelle selbst herunterladen |

Alles andere, was eine Maschine braucht – das Runtime-Image und die Modelle –, kommt vom Server. Eine Maschine
ohne Internetzugang funktioniert, solange der Server die Agent-Builds vorhält; siehe
[Maschinen ohne Internetzugang](machines.md#machines-without-internet-access).
