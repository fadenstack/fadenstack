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
Python-Abhängigkeiten, Python selbst und die nativen Bibliotheken, die es nutzt (OpenSSL, zlib, libffi, xz und
andere): Jedes Release legt deren Lizenztexte neben das Programm. Die Images, die die Dateien der
Installation nennen, werden separat heruntergeladen und behalten ihre eigenen Bedingungen.

## Auf den GPU-Maschinen {#on-the-gpu-machines}

| Artefakt | Basiert auf | Bedingungen |
|---|---|---|
| Runtime-Image für NVIDIA DGX Spark | Das eigene Image von vLLM (`vllm/vllm-openai`, sein CUDA-13-Build für arm64), mit CUDA und verwandten Paketen von NVIDIA, dazu Ray und zusätzliche Python-Pakete | vLLM und Ray stehen unter Apache-2.0. Die Software von NVIDIA darin (CUDA-Bibliotheken, Entwicklerwerkzeuge, die CUTLASS DSL) steht unter den Bedingungen von NVIDIA, die nicht Open Source sind: siehe [NVIDIA-Komponenten](#nvidia-components). Ihre Lizenzdateien bleiben im Image. |
| Runtime-Image für x86_64-Maschinen | Ein schlankes Python-Basis-Image, Betriebssystempakete, Ray und vLLM aus PyPI und die Python- und CUDA-Pakete, die diese nachziehen | Jede Komponente behält ihre eigene Lizenz. Die Software von NVIDIA darin (CUDA, cuDNN und verwandte Pakete, Entwicklerwerkzeuge, die CUTLASS DSL) steht unter den Bedingungen von NVIDIA, die nicht Open Source sind: siehe [NVIDIA-Komponenten](#nvidia-components). Ihre Lizenzdateien bleiben im Image; die SBOM ist das Verzeichnis |
| Modell-TLS-Proxy | nginx | Die Lizenz von nginx |
| Die Modelle | Was Sie von Hugging Face bereitstellen oder importieren | Die eigene Lizenz jedes Modells, die der Administrator bei zugangsbeschränkten Modellen auf Hugging Face akzeptiert |

Die Maschinen erhalten diese Images vom Server.

!!! note
    Wenn Sie ein Runtime-Image außerhalb Ihrer Organisation weitergeben, prüfen Sie vorher die genauen Bedingungen
    seines Basis-Images.

## NVIDIA-Komponenten in den Runtime-Images {#nvidia-components}

Beide Runtime-Images enthalten Software von NVIDIA so, wie ihre Upstream-Projekte (vLLM, PyTorch, Triton) und
NVIDIAs eigene Python-Pakete sie verbreiten:

| Komponente | Herkunft | Bedingungen |
|---|---|---|
| CUDA-Bibliotheken (Runtime, cuBLAS, cuDNN, NCCL und andere) | NVIDIAs Python-Pakete (`nvidia-*`); im Image für DGX Spark auch das Basis-Image von vLLM | [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) und die eigene Lizenz des jeweiligen Pakets |
| Entwicklerwerkzeuge: `ptxas`, `nvcc`, `nvdisasm`, `cuobjdump` | Das Basis-Image von vLLM, PyTorch, Triton und NVIDIAs Python-Pakete, die damit im laufenden Betrieb GPU-Code übersetzen | [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) |
| CUTLASS DSL (`nvidia-cutlass-dsl`) | Eine Abhängigkeit von vLLM | [NVIDIA CUTLASS DSL EULA](https://docs.nvidia.com/cutlass/4.6.0/media/docs/pythonDSL/license.html) |

### In Prüfung {#under-review}

Die Bedingungen von NVIDIA gestatten die Weitergabe einiger dieser Komponenten nicht ausdrücklich: Die Liste der
weitergebbaren Dateien in der CUDA EULA (Anhang A) nennt die Entwicklerwerkzeuge nicht, und die Lizenz der CUTLASS
DSL gestattet nur die Weitergabe ihrer Python-Quelldateien. Große Open-Source-Projekte für Inferenz geben diese
Komponenten auf dieselbe Weise weiter, und die Frage wurde öffentlich gestellt, etwa in
[triton-lang/triton#1618](https://github.com/triton-lang/triton/issues/1618), bislang ohne Antwort von NVIDIA.

Auf Grundlage der verfügbaren Informationen prüfen wir das und werden NVIDIA um Klärung bitten. Bestätigt die
Prüfung, dass eine Komponente in unseren Images nicht weitergegeben werden darf, entfernen wir sie aus künftigen
Images und ändern, wie sie auf Ihre GPU-Maschinen gelangt, etwa indem die Maschinen sie direkt von NVIDIA
herunterladen, mit einem Download-Schritt für Installationen ohne Internetzugang. Bis dahin enthalten die Images
diese Komponenten so, wie ihre Upstream-Projekte sie verbreiten.

Fadenstack ist Software vor Version 1.0 und befindet sich in der Erprobung. Rechteinhaber, die Bedenken zu einer
Komponente in unseren Images haben, erreichen uns über [emagin8.de/contact](https://emagin8.de/contact?subject=Fadenstack); wir
antworten zeitnah.

Die Nutzung von NVIDIA-Software unterliegt den Bedingungen von NVIDIA. Diese Seite dient der Transparenz; sie ist
keine Rechtsberatung und ändert nicht die Bedingungen einer Komponente.

## Quellen {#sources}

- [Ray](https://github.com/ray-project/ray)
- [vLLM](https://github.com/vllm-project/vllm)
- [Swagger UI](https://github.com/swagger-api/swagger-ui)
- [ReDoc](https://github.com/Redocly/redoc)
- [Sora](https://github.com/sora-xor/sora-font)
- [Inter](https://github.com/rsms/inter)
- [IBM Plex](https://github.com/IBM/plex)
- [Zensical](https://github.com/zensical/zensical)
- [Lucide](https://github.com/lucide-icons/lucide)
- [Material Design Icons](https://github.com/Templarian/MaterialDesign)
- [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html)
- [NVIDIA CUTLASS DSL EULA](https://docs.nvidia.com/cutlass/4.6.0/media/docs/pythonDSL/license.html)
