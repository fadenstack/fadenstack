"""A fake vLLM-Metal runtime and its carrier image, for the native-runtime tests.

The runtime has the C1 layout (``runtime/{python,venv,RUNTIME-MANIFEST.json}``);
its ``venv/bin/vllm`` runs ``fake_vllm.py`` with the test's own Python. The
fake serves ``/v1/models`` and starts two children, one of which ignores
SIGTERM, so a stop must take the whole tree.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

FAKE_VLLM = textwrap.dedent(
    '''
    import http.server, json, os, signal, subprocess, sys, time

    args = sys.argv[1:]
    assert args[0] == "serve", args
    port = int(args[args.index("--port") + 1])
    served = args[args.index("--served-model-name") + 1]
    mode = os.environ.get("VLLM_FAKE_MODE", "ok")
    home = os.environ["HOME"]
    with open(os.path.join(home, f"env-{port}.json"), "w") as fh:
        json.dump(dict(os.environ), fh)
    # What a hostile worker would try: the agent's state beside the native root.
    state = os.path.join(home, "..", "..", "state.json")
    probe = {}
    try:
        with open(state) as fh:
            probe["read"] = fh.read()
    except OSError as exc:
        probe["read"] = f"denied: {exc.strerror}"
    try:
        with open(os.path.join(home, "..", "..", "planted"), "w") as fh:
            fh.write("x")
        probe["write"] = "written"
    except OSError as exc:
        probe["write"] = f"denied: {exc.strerror}"
    with open(os.path.join(home, f"probe-{port}.json"), "w") as fh:
        json.dump(probe, fh)
    print("INFO fake vllm starting", flush=True)
    if mode == "exit":
        print("ValueError: boom, no Metal device", flush=True)
        sys.exit(3)
    kids = [
        subprocess.Popen([sys.executable, "-c",
                          "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"]),
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"]),
    ]
    if os.environ.get("VLLM_FAKE_SETSID") == "1":
        # A helper in a session of its own: neither in the group nor, once
        # the leader dies, a descendant.
        kids.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True))
    with open(os.path.join(home, f"kids-{port}.txt"), "w") as fh:
        fh.write(" ".join(str(k.pid) for k in kids))
    if mode == "never":
        time.sleep(600)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"object": "list", "data": [{"id": served, "object": "model"}]}).encode()
            self.send_response(200 if self.path == "/v1/models" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    time.sleep(0.3)
    http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
    '''
)


def build_runtime(root: Path, *, python: str = sys.executable, manifest: dict | None = None) -> Path:
    """``root/runtime/...``; returns ``root/runtime``."""
    rt = root / "runtime"
    (rt / "python" / "bin").mkdir(parents=True)
    (rt / "venv" / "bin").mkdir(parents=True)
    interp = rt / "python" / "bin" / "python3.12"
    interp.write_text("#!/bin/sh\nexit 0\n")
    interp.chmod(0o755)
    os.symlink("../../python/bin/python3.12", rt / "venv" / "bin" / "python")
    (rt / "venv" / "pyvenv.cfg").write_text(
        "home = /build/somewhere/python/bin\nimplementation = CPython\nrelocatable = true\n"
    )
    (rt / "venv" / "fake_vllm.py").write_text(FAKE_VLLM)
    vllm = rt / "venv" / "bin" / "vllm"
    vllm.write_text(f'#!/bin/sh\nexec "{python}" "$(dirname -- "$0")/../fake_vllm.py" "$@"\n')
    vllm.chmod(0o755)
    (rt / "RUNTIME-MANIFEST.json").write_text(json.dumps(manifest if manifest is not None else {
        "schema_version": "1.0.0",
        "kind": "python-venv",
        "target": {"os": "darwin", "cpu": "arm64"},
        "interpreter": {"implementation": "cpython", "version": "3.12.12", "path": "python/bin/python3.12"},
        "venv": "venv",
        "engines": {"vllm": "0.34.0", "vllm_metal": "0.34.0", "mlx": "0.32.4", "mlx_lm": "0.32.3"},
        "patches": {"mlx_lm_fd_cap_raise_only": True},
    }))
    return rt


def layer_from_dir(root: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.add(root / "runtime", arcname="runtime")
    return buf.getvalue()


def layer_from_members(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class Carrier:
    tar: Path
    config_digest: str
    layer_digest: str
    size_bytes: int


def carrier(path: Path, layer: bytes, *, compress: bool = False, os_name: str = "darwin",
            diff_id: str | None = None) -> Carrier:
    """A ``docker save`` tar (manifest.json + config + one layer) of *layer*."""
    layer_digest = diff_id or sha(layer)
    config = json.dumps({
        "architecture": "arm64", "os": os_name,
        "rootfs": {"type": "layers", "diff_ids": [layer_digest]},
        "config": {"Labels": {"org.fadenstack.runtime.kind": "native-carrier"}},
    }).encode()
    blob = gzip.compress(layer) if compress else layer
    entries = [
        (f"blobs/sha256/{sha(config)[7:]}", config),
        (f"blobs/sha256/{sha(blob)[7:]}", blob),
    ]
    manifest = json.dumps([{"Config": entries[0][0], "RepoTags": ["x:y"], "Layers": [entries[1][0]]}]).encode()
    with tarfile.open(path, "w") as tar:
        for name, data in [("manifest.json", manifest), *entries]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return Carrier(path, sha(config), layer_digest, len(blob))


def file_member(name: str, data: bytes = b"x", mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info, data


def link_member(name: str, target: str, *, hard: bool = False) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE if hard else tarfile.SYMTYPE
    info.linkname = target
    return info, None


def special_member(name: str, kind: bytes) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    return info, None
