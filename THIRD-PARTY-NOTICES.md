# Third-party notices

Fadenstack's own code is under the [Apache License 2.0](LICENSE) (see [NOTICE](NOTICE)). Some files in this
repository started from other projects, and the images and binaries built from it contain third-party software.
This file gives the notices for the first, and says where the licence texts of the second are.

## Code that started from other projects

### FastAPI-template

The console API (`backend`), the gateway (`gateway`) and the PII service (`pii`) were generated with
[FastAPI-template](https://github.com/s3rius/FastAPI-template) and have changed a great deal since. What is left
of the template is in their application and lifespan modules, `__main__.py`, the monitoring routes
(`web/api/monitoring/`) and `db/utils.py`.

```text
MIT License

Copyright (c) 2022 Pavel Kirilin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### loguru

`InterceptHandler` in `backend/faden_backend/log.py` and `gateway/faden_gateway/log.py` follows the example in
[loguru](https://github.com/Delgan/loguru)'s documentation.

```text
MIT License

Copyright (c) 2017

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Zensical

`docs/theme/overrides/partials/header.html` and `alternate.html` are adapted from
[Zensical](https://github.com/zensical/zensical)'s templates. Copyright (c) 2025-2026 Zensical and
contributors, MIT License; the full text is `docs/theme/assets/licenses/zensical-LICENSE.md`, and ships with Help.

## Files kept as they are, with their licences beside them

| What | Where | Licence |
| --- | --- | --- |
| Swagger UI and ReDoc, for the API documentation pages | `backend`, `gateway`, `pii`: `.../static/docs/` | Swagger UI: Apache-2.0 (its `LICENSE` and `NOTICE` beside it). ReDoc: MIT |
| IBM Plex Sans and IBM Plex Mono, IBM's own web subsets | `docs/theme/assets/fonts/` | SIL Open Font License 1.1, with Reserved Font Name "Plex": `OFL-IBM-Plex.txt` |
| Sora | `docs/theme/assets/fonts/` | SIL Open Font License 1.1: `OFL-Sora.txt` |
| Help's icons: Lucide, Material Design Icons | through Zensical | ISC and the Pictogrammers Free License: `docs/theme/assets/licenses/` |

## What the images and binaries contain

- **Every image** keeps the licence files of what it contains: Python packages (`*.dist-info`), npm packages
  (`node_modules`) and operating-system packages (`/usr/share/doc/*/copyright`). Each release publishes SPDX
  SBOMs that list them. The source of the GNU GPL and LGPL operating-system packages is on
  [snapshot.debian.org](https://snapshot.debian.org) for the versions the SBOMs name. We also provide it on
  request, for three years after each release: <https://emagin8.de/contact?subject=Fadenstack%20source%20request>.
- **The console** bundles npm packages and the Inter and Sora fonts (SIL OFL 1.1) into minified files. Their
  licence texts are served by the console at `/third-party-licenses.md` (written by the build) and
  `/licenses/supplement.md` (what the build tools add).
- **The console API and gateway images** contain lxml, which links GNU libiconv (LGPL-2.1).
- **The standalone `faden` and `faden-agent` binaries** bundle their Python dependencies, the Python they were
  built with, PyInstaller's bootloader and native libraries: OpenSSL, libffi, xz, zlib, bzip2, libmpdec, expat,
  libuuid and others, depending on the platform (the Windows binary also the Microsoft C runtime). Each release
  attaches their licence texts next to the binary. For the libraries, the Linux builds take the copyright files
  of the Debian packages they come from; the macOS and Windows builds take the texts in
  [`release/licenses/`](release/licenses), and the build fails if a bundled library has none.
- **The GPU runtime images** contain NVIDIA's CUDA libraries under NVIDIA's terms (the CUDA EULA), so they
  are not Apache-2.0 as a whole: see [Help, Third-party components](docs/help/pages/operate/third-party.md).

Help's [Third-party components](docs/help/pages/operate/third-party.md) page describes all of this for people
running Fadenstack.
