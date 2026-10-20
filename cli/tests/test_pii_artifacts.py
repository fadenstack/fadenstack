"""Pinned downloads are checked before an installation can adopt them."""

import hashlib

import httpx
import pytest

from faden_cli.core import pii_artifacts


@pytest.mark.parametrize("bad", [None, "revision", "checksum"])
def test_provision_is_pinned_checked_and_atomic(tmp_path, monkeypatch, bad):
    backend = "gliner-onnx"
    _, revision, _ = pii_artifacts.MODELS[backend]
    files = {"gliner_config.json": b"{}", "onnx/fp16/model_fp16.onnx": b"fixture"}
    siblings = [
        {"rfilename": name, "size": len(data), "lfs": {"sha256": hashlib.sha256(data).hexdigest()}}
        for name, data in files.items()
    ]

    def handler(request):
        if "/api/models/" in request.url.path:
            return httpx.Response(
                200, json={"sha": "wrong" if bad == "revision" else revision, "siblings": siblings}
            )
        name = request.url.path.split(f"/resolve/{revision}/")[1]
        return httpx.Response(200, content=b"corrupt" if bad == "checksum" else files[name])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(pii_artifacts.httpx, "Client", lambda **kw: client)
    root = tmp_path / "model"
    if bad:
        with pytest.raises(ValueError):
            pii_artifacts.provision(backend, root)
        assert not root.exists()
        assert not list(tmp_path.glob(".pii-download-*"))
    else:
        assert pii_artifacts.provision(backend, root) == root
        assert (root / "manifest.json").is_file()
        assert (root / "onnx/fp16/model_fp16.onnx").read_bytes() == b"fixture"
        assert pii_artifacts.provision(backend, root) == root
