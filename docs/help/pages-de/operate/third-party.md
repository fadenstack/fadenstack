# Komponenten von Drittanbietern {#third-party-components}

Was der Server und die GPU-Maschinen außer dem eigenen Code von Fadenstack ausführen und wessen Bedingungen dafür
gelten. Diese Seite ist ein Nachschlagewerk für Lizenz- und Compliance-Prüfungen.

Der eigene Code von Fadenstack, seine Konfiguration und seine Dateien der Installation stehen unter der
Apache License 2.0: die Dienste des Servers, das Kommandozeilenwerkzeug `faden`, der Node-Agent und der
Runtime-Helfer. Diese Lizenz ändert nichts an den Bedingungen der unten aufgeführten Komponenten. Zu jedem
Release werden SPDX-Softwarestücklisten (SBOMs) für seine Artefakte veröffentlicht; sie listen die darin
gefundenen Pakete auf. Eine SBOM ist ein Verzeichnis, keine Einräumung von Rechten: Maßgeblich sind die
Lizenzdateien und -hinweise in jedem Image.

## Auf dem Server {#on-the-server}

`faden deploy` lädt die eigenen Images von Fadenstack und diese Images von Drittanbietern herunter, jedes unter
seinen eigenen Bedingungen:

| Komponente | Was sie hier tut |
|---|---|
| nginx | Der Eingang: HTTPS, die Konsole, `/api`, `/v1` |
| PostgreSQL mit pgvector | Alle relationalen Daten und die Vektoren für die Dokumentsuche |
| Redis | Leases, Ratenbegrenzungen, Cache |
| RabbitMQ | Nachrichten zwischen den Diensten |
| ClickHouse | Der Trace-Speicher hinter Langfuse |
| MinIO | Der Dateispeicher hinter Langfuse |
| Langfuse (web und worker) | Traces der Anfragen, wenn das Tracing eingeschaltet ist (standardmäßig ist es aus) |
| Prometheus | Metriken |
| Loki | Logs |
| Grafana | Dashboards |
| Grafana Alloy | Sammelt die Container-Logs des Servers |

In den eigenen Images von Fadenstack:

| Image | Inhalte von Drittanbietern | Bedingungen |
|---|---|---|
| Alle Dienst-Images | Python-Pakete aus den Lock-Dateien der Dienste, das Python-Basis-Image, Betriebssystempakete | Jedes behält seine eigene Lizenz; die SBOM des Images listet sie auf |
| Konsole (Frontend) | npm-Pakete (React, React Router, MUI und andere); die Schrift Sora | Jedes npm-Paket behält seine eigene Lizenz; Sora steht unter der SIL Open Font License 1.1 |
| Konsolen-API, Gateway, PII | Swagger UI und ReDoc, für die Seiten der API-Dokumentation | Swagger UI: Apache License 2.0. ReDoc: MIT. Ihre Lizenz- und Hinweisdateien liegen daneben |

Das Werkzeug `faden` enthält die Dateien der Installation und seine Python-Abhängigkeiten, die ihre eigenen Lizenzen
behalten. Die Images, die diese Dateien nennen, werden separat heruntergeladen und behalten ihre eigenen
Bedingungen.

## Auf den GPU-Maschinen {#on-the-gpu-machines}

| Artefakt | Basiert auf | Bedingungen |
|---|---|---|
| Runtime-Image für NVIDIA DGX Spark | Der vLLM-Container von NVIDIA aus dem NVIDIA NGC catalog (`nvcr.io/nvidia/vllm`), mit CUDA und weiteren NVIDIA-Komponenten, dazu Ray und zusätzliche Python-Pakete | Das Basis-Image steht unter der [NVIDIA Deep Learning Container License](https://developer.download.nvidia.com/licenses/NVIDIA_Deep_Learning_Container_License.pdf), die Bedingungen für die Weitergabe abgeleiteter Container festlegt und die eigenständige Weitergabe einschränkt. Ray und vLLM stehen unter Apache-2.0, aber dadurch steht nicht das ganze Image unter Apache-2.0 |
| Runtime-Image für x86_64-Maschinen | Ein schlankes Python-Basis-Image, Betriebssystempakete, Ray und vLLM aus PyPI und die Python- und CUDA-Pakete, die diese nachziehen | Jede Komponente behält ihre eigene Lizenz, auch NVIDIA-Pakete, die nicht Open Source sind. Die SBOM des Images ist das Verzeichnis |
| Modell-TLS-Proxy | nginx | Die Lizenz von nginx |
| Die Modelle | Was Sie von Hugging Face bereitstellen oder importieren | Die eigene Lizenz jedes Modells, die der Administrator bei zugangsbeschränkten Modellen auf Hugging Face akzeptiert |

Die Maschinen erhalten diese Images vom Server.

!!! note
    Wenn Sie ein Runtime-Image außerhalb Ihrer Organisation weitergeben, prüfen Sie vorher die genauen Bedingungen
    seines Basis-Images.

## Quellen {#sources}

- [Ray](https://github.com/ray-project/ray)
- [vLLM](https://github.com/vllm-project/vllm)
- [vLLM-Container von NVIDIA](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/running.html)
- [Swagger UI](https://github.com/swagger-api/swagger-ui)
- [ReDoc](https://github.com/Redocly/redoc)
- [Sora](https://github.com/sora-xor/sora-font)
