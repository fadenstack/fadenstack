# Third-party components

What the server and the GPU machines run besides Fadenstack's own code, and whose terms apply. This page is a
reference for licence and compliance reviews.

Fadenstack's own code, configuration and deployment files are under the Apache License 2.0: the server's
services, the `faden` command-line tool, the node agent and the runtime helper. That licence does not change the
terms of the components below. Each release publishes SPDX software bills of materials (SBOMs) for its
artifacts; they list the packages found in them. An SBOM is an inventory, not a grant of rights: the licence files
and notices inside each image are what count.

## On the server

`faden deploy` pulls Fadenstack's own images and these third-party images, each under its own terms:

| Component | What it does here |
|---|---|
| nginx | The front door: HTTPS, the console, `/api`, `/v1` |
| PostgreSQL with pgvector | All relational data, and vectors for document search |
| Redis | Leases, rate limits, cache |
| RabbitMQ | Messages between the services |
| ClickHouse | The trace store behind Langfuse |
| MinIO | File storage behind Langfuse |
| Langfuse (web and worker) | Request traces, when tracing is switched on (it is off by default) |
| Prometheus | Metrics |
| Loki | Logs |
| Grafana | Dashboards |
| Grafana Alloy | Collects the server's container logs |

Inside Fadenstack's own images:

| Image | Third-party content | Terms |
|---|---|---|
| All service images | Python packages from the services' lock files, the Python base image, operating-system packages | Each keeps its own licence; the image's SBOM lists them |
| Console (frontend) | npm packages (React, React Router, MUI and others); the Inter and Sora fonts, served by the console itself | Each npm package keeps its own licence; Inter and Sora are under the SIL Open Font License 1.1 |
| Console API, gateway, PII | Swagger UI and ReDoc, for the API documentation pages | Swagger UI: Apache License 2.0. ReDoc: MIT. Their licence and notice files ship next to them |

The `faden` tool carries the deployment files and its Python dependencies, which keep their own licences. The
images those files name are downloaded separately and keep their own terms.

## On the GPU machines

| Artifact | Built on | Terms |
|---|---|---|
| Runtime image for NVIDIA DGX Spark | NVIDIA's vLLM container from the NVIDIA NGC catalog (`nvcr.io/nvidia/vllm`), with CUDA and other NVIDIA components, plus Ray and added Python packages | The base image is under the [NVIDIA Deep Learning Container License](https://developer.download.nvidia.com/licenses/NVIDIA_Deep_Learning_Container_License.pdf), which sets conditions on distributing derived containers and restricts stand-alone redistribution. Ray and vLLM are Apache-2.0, but that does not make the whole image Apache-2.0 |
| Runtime image for x86_64 machines | A slim Python base image, operating-system packages, Ray and vLLM from PyPI, and the Python and CUDA packages they pull in | Each component keeps its own licence, including NVIDIA packages that are not open source. The image's SBOM is the inventory |
| Model-TLS proxy | nginx | nginx's licence |
| The models | Whatever you deploy from Hugging Face or import | Each model's own licence, which the administrator accepts on Hugging Face for gated models |

The machines get these images from the server.

!!! note
    If you redistribute a runtime image outside your organisation, check the exact terms of its base image first.

## Sources

- [Ray](https://github.com/ray-project/ray)
- [vLLM](https://github.com/vllm-project/vllm)
- [NVIDIA vLLM container](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/running.html)
- [Swagger UI](https://github.com/swagger-api/swagger-ui)
- [ReDoc](https://github.com/Redocly/redoc)
- [Sora](https://github.com/sora-xor/sora-font)
- [Inter](https://github.com/rsms/inter)
