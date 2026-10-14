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
| All service images | Python packages from the services' lock files, the Python base image, operating-system packages | Each keeps its own licence, and its licence files stay in the image; the image's SBOM lists them. The console API and gateway images contain lxml, which links GNU libiconv (LGPL-2.1). The source of the operating-system packages under the GPL or LGPL is on [snapshot.debian.org](https://snapshot.debian.org) for the versions the SBOM names |
| Console (frontend) | npm packages (React, React Router, MUI and others); the Inter and Sora fonts, served by the console itself | Each npm package keeps its own licence; Inter and Sora are under the SIL Open Font License 1.1. The console serves the licence texts of everything bundled into it at `/third-party-licenses.md`, and of what the build tools add at `/licenses/supplement.md` |
| Help (served by the console at `/help`) | Zensical, its icons (Lucide, Material Design Icons), the IBM Plex Sans, IBM Plex Mono and Sora fonts | Zensical: MIT. Lucide: ISC. Material Design Icons: Pictogrammers Free License. The fonts: SIL Open Font License 1.1; "Plex" is a Reserved Font Name, so the IBM Plex files are IBM's own, unmodified. The texts are at `/help/assets/licenses/` and `/help/assets/fonts/` |
| Console API, gateway, PII | Swagger UI and ReDoc, for the API documentation pages | Swagger UI: Apache License 2.0. ReDoc: MIT. Their licence and notice files ship next to them |

The `faden` tool carries the deployment files; pip installs its Python dependencies, which keep their own
licences. The standalone `faden` and `faden-agent` binaries bundle their Python dependencies, the Python
itself and the native libraries it uses (OpenSSL, zlib, libffi, xz and others): each release attaches their
licence texts next to the binary. The images the deployment files name are downloaded separately
and keep their own terms.

## On the GPU machines

| Artifact | Built on | Terms |
|---|---|---|
| Runtime image for NVIDIA DGX Spark | vLLM's own image (`vllm/vllm-openai`, its CUDA 13 build for arm64), with NVIDIA's CUDA and related packages, plus Ray and added Python packages | vLLM and Ray are Apache-2.0. NVIDIA's software in it (CUDA libraries, developer tools, the CUTLASS DSL) is under NVIDIA's terms, which are not open source: see [NVIDIA components](#nvidia-components). Their licence files stay in the image. |
| Runtime image for x86_64 machines | A slim Python base image, operating-system packages, Ray and vLLM from PyPI, and the Python and CUDA packages they pull in | Each component keeps its own licence. NVIDIA's software in it (CUDA, cuDNN and related packages, developer tools, the CUTLASS DSL) is under NVIDIA's terms, which are not open source: see [NVIDIA components](#nvidia-components). Their licence files stay in the image; its SBOM is the inventory |
| Model-TLS proxy | nginx | nginx's licence |
| The models | Whatever you deploy from Hugging Face or import | Each model's own licence, which the administrator accepts on Hugging Face for gated models |

The machines get these images from the server.

!!! note
    If you redistribute a runtime image outside your organisation, check the exact terms of its base image first.

## NVIDIA components in the runtime images {#nvidia-components}

Both runtime images contain NVIDIA software as their upstream projects (vLLM, PyTorch, Triton) and NVIDIA's own
Python packages distribute it:

| Component | Where it comes from | Terms |
|---|---|---|
| CUDA libraries (runtime, cuBLAS, cuDNN, NCCL and others) | NVIDIA's Python packages (`nvidia-*`); in the DGX Spark image also vLLM's base image | [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) and each package's own licence |
| Developer tools: `ptxas`, `nvcc`, `nvdisasm`, `cuobjdump` | vLLM's base image, PyTorch, Triton and NVIDIA's Python packages, which use them to compile GPU code while models run | [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) |
| CUTLASS DSL (`nvidia-cutlass-dsl`) | A dependency of vLLM | [NVIDIA CUTLASS DSL EULA](https://docs.nvidia.com/cutlass/4.6.0/media/docs/pythonDSL/license.html) |

### Under review

NVIDIA's terms do not expressly grant redistribution of some of these components: the CUDA EULA's list of
distributable files (Attachment A) does not name the developer tools, and the CUTLASS DSL's licence grants
distribution of its Python source files only. Large open-source inference projects distribute these components in
the same way, and the question has been raised publicly, for example in
[triton-lang/triton#1618](https://github.com/triton-lang/triton/issues/1618), without an answer from NVIDIA so far.

Based on the information available, we are reviewing this and will seek clarification from NVIDIA. If the review
confirms that a component may not be redistributed in our images, we will remove it from future images and change
how it reaches your GPU machines, for example by having the machines download it from NVIDIA directly, with a
download step for installations without internet access. Until then, the images contain these components as their
upstream projects distribute them.

Fadenstack is pre-1.0 software in active testing. Rights holders who have concerns about a component in our images
can contact us through [emagin8.de/contact](https://emagin8.de/contact?subject=Fadenstack); we will respond promptly.

Your use of NVIDIA software is subject to NVIDIA's terms. This page is provided for transparency; it is not legal
advice and does not change the terms of any component.

## Sources

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
