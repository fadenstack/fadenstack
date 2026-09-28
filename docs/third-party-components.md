# Third-party components in release artifacts

Apache-2.0 covers Fadenstack-authored code, configuration, and deployment
files. It does not change the terms of dependencies, container bases, fonts,
or the assets described here. The SPDX SBOMs attached to each release record
the packages found in the published artifacts; inspect an image's own license
files and notices for its complete terms.

| Artifact | Third-party content | Terms to preserve/check |
| --- | --- | --- |
| CLI wheel, source archive, and standalone executables | Python CLI dependencies; `shared/` deployment configs and Docker Compose references to Nginx, PostgreSQL, ClickHouse, Redis, RabbitMQ, Prometheus, Loki, Grafana, Alloy, and the Fadenstack images | The configs are Fadenstack-authored. The referenced images are downloaded separately and retain their own terms. Bundled Python dependencies keep their own licenses. |
| Core service images | Python packages from service lockfiles, the Python/uv base image, OS packages; frontend Node packages and `node:20-alpine` | Image SBOMs inventory resolved packages. Keep license and notice files from dependencies and base images. |
| Frontend image | `@fontsource/sora` font files in the compiled frontend; npm dependencies including React, React Router, MUI, and others | Sora uses the SIL Open Font License 1.1; npm packages retain their individual terms. Fadenstack artwork and marks remain subject to `OFFICIAL.md`. |
| Backend, gateway, and PII images | Pinned Swagger UI Dist 5.33.0 and ReDoc 2.5.4 assets under each service's `static/docs/` | Swagger UI is Apache-2.0; ReDoc is MIT. Their exact npm distributions supplied the adjacent upstream license, Swagger UI NOTICE, and generated `*.LICENSE.txt` files. The minified bundles reference those generated notices. |
| GB10 runtime image | `nvcr.io/nvidia/vllm:26.08-py3`, including NVIDIA CUDA and other GPU components; Ray and added Python packages | The base image is governed by the [NVIDIA Deep Learning Container License](https://developer.download.nvidia.com/licenses/NVIDIA_Deep_Learning_Container_License.pdf), including conditions on derived-container distribution and a restriction on stand-alone redistribution. Ray and upstream vLLM code use Apache-2.0, but that does not make the entire NVIDIA image Apache-2.0. Verify the exact base-image license files for the published digest. |
| x86_64 runtime image | `python:3.12.14-slim-trixie`, OS packages, `ray[serve,llm]==2.58.0`, `vllm==0.26.0`, transitive Python/CUDA packages | Each installed component keeps its own license, including any proprietary NVIDIA packages pulled transitively. The published image SBOM is the inventory to reconcile with installed license files. |

Before release, review the SPDX documents for unidentified licenses and
packages, preserve required notices in each redistributed image/archive, and
check the exact NVIDIA base-image terms. An SBOM is an inventory, not a grant
of rights or a substitute for the license texts.

Sources: [Swagger UI](https://github.com/swagger-api/swagger-ui),
[ReDoc](https://github.com/Redocly/redoc),
[Sora](https://github.com/sora-xor/sora-font),
[Ray](https://github.com/ray-project/ray),
[vLLM](https://github.com/vllm-project/vllm),
[NVIDIA vLLM container docs](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/running.html).
