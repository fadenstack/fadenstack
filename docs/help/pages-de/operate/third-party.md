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
| Alle Dienst-Images | Python-Pakete aus den Lock-Dateien der Dienste, das Python-Basis-Image, Betriebssystempakete | Jedes behält seine eigene Lizenz, und seine Lizenzdateien bleiben im Image; die SBOM des Images listet sie auf. Die Images der Konsolen-API und des Gateways enthalten lxml, das GNU libiconv (LGPL-2.1) einbindet. Den Quellcode der Betriebssystempakete unter GPL oder LGPL gibt es auf [snapshot.debian.org](https://snapshot.debian.org) für die Versionen, die die SBOM nennt |
| Konsole (Frontend) | npm-Pakete (React, React Router, MUI und andere); die Schriften Inter und Sora, ausgeliefert von der Konsole selbst | Jedes npm-Paket behält seine eigene Lizenz; Inter und Sora stehen unter der SIL Open Font License 1.1. Die Konsole liefert die Lizenztexte von allem, was in ihr gebündelt ist, unter `/third-party-licenses.md` aus, und die von dem, was die Build-Werkzeuge hinzufügen, unter `/licenses/supplement.md` |
| Hilfe (von der Konsole unter `/help` ausgeliefert) | Zensical, seine Symbole (Lucide, Material Design Icons), die Schriften IBM Plex Sans, IBM Plex Mono und Sora | Zensical: MIT. Lucide: ISC. Material Design Icons: Pictogrammers Free License. Die Schriften: SIL Open Font License 1.1; „Plex“ ist ein reservierter Schriftname, deshalb sind die IBM-Plex-Dateien die von IBM, unverändert. Die Texte liegen unter `/help/assets/licenses/` und `/help/assets/fonts/` |
| Konsolen-API, Gateway, PII | Swagger UI und ReDoc, für die Seiten der API-Dokumentation | Swagger UI: Apache License 2.0. ReDoc: MIT. Ihre Lizenz- und Hinweisdateien liegen daneben |

Das Werkzeug `faden` enthält die Dateien der Installation; seine Python-Abhängigkeiten installiert pip, und sie
behalten ihre eigenen Lizenzen. Die eigenständigen Programme `faden` und `faden-agent` bündeln ihre
Python-Abhängigkeiten: Jedes Release legt deren Lizenztexte neben das Programm. Die Images, die die Dateien der
Installation nennen, werden separat heruntergeladen und behalten ihre eigenen Bedingungen.

## Auf den GPU-Maschinen {#on-the-gpu-machines}

| Artefakt | Basiert auf | Bedingungen |
|---|---|---|
| Runtime-Image für NVIDIA DGX Spark | Das eigene Image von vLLM (`vllm/vllm-openai`, sein CUDA-13-Build für arm64), mit CUDA und verwandten Paketen von NVIDIA, dazu Ray und zusätzliche Python-Pakete | vLLM und Ray stehen unter Apache-2.0; die CUDA-Pakete von NVIDIA unter der [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) und ihren eigenen Bedingungen, die nicht Open Source sind. Ihre Lizenzdateien bleiben im Image. Runtimes auf Basis des vLLM-Containers von NVIDIA (`nvcr.io/nvidia/vllm`, benannt `...-nv<Release>`) fallen unter das [NVIDIA Software License Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/) und die [Product-Specific Terms for NVIDIA AI Products](https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-ai-products/), die ihre Veröffentlichung nicht erlauben; sie werden nicht zum Download angeboten |
| Runtime-Image für x86_64-Maschinen | Ein schlankes Python-Basis-Image, Betriebssystempakete, Ray und vLLM aus PyPI und die Python- und CUDA-Pakete, die diese nachziehen | Jede Komponente behält ihre eigene Lizenz, auch CUDA, cuDNN und verwandte Pakete von NVIDIA unter der [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) und ihren eigenen Bedingungen, die nicht Open Source sind. Ihre Lizenzdateien bleiben im Image; die SBOM ist das Verzeichnis |
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
- [Inter](https://github.com/rsms/inter)
- [IBM Plex](https://github.com/IBM/plex)
- [Zensical](https://github.com/zensical/zensical)
- [Lucide](https://github.com/lucide-icons/lucide)
- [Material Design Icons](https://github.com/Templarian/MaterialDesign)
- [NVIDIA Software License Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/) und [Product-Specific Terms for NVIDIA AI Products](https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-ai-products/)
- [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html)
