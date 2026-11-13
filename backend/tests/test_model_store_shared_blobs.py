"""Removing a model's files frees what only that model used, in the cache's shared store too.

Since huggingface_hub 2.x a downloaded file lives once for the whole cache, in
``<root>/blobs/<xx>/<hash>``, and the model's ``blobs/`` holds a link to it.
Removing the model's folder alone left the files themselves behind. The cache
here is laid out by the library's own functions, as a download lays it out.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from huggingface_hub.utils._shared_blobs import publish_blob_to_shared_store, try_link_from_shared_store

from faden_backend.services.llm.service import _remove_tree

WEIGHTS = b"W" * 10
CONFIG = b"config"
XET_HASH = "ab" + "1" * 62
COMMIT = "c" * 40
#: What a model folder holds of its own: the config and the ref naming its commit.
OWN = len(CONFIG) + len(COMMIT)


def _repo(root: Path, name: str, etag: str, *, share: bool) -> Path:
    """A model folder holding one shared weights file and one file of its own."""
    repo = root / f"models--org--{name}"
    blobs = repo / "blobs"
    snapshot = repo / "snapshots" / COMMIT
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(COMMIT)
    (blobs / ("f" * 40)).write_bytes(CONFIG)
    weights = blobs / etag
    if share:
        if not try_link_from_shared_store(
            blob_path=str(weights), xet_hash=XET_HASH, cache_dir=root, expected_size=len(WEIGHTS),
        ):
            weights.write_bytes(WEIGHTS)
            assert publish_blob_to_shared_store(
                blob_path=str(weights), xet_hash=XET_HASH, cache_dir=root, expected_size=len(WEIGHTS),
            )
    else:
        weights.write_bytes(WEIGHTS)
    try:
        (snapshot / "model.safetensors").symlink_to(Path("..", "..", "blobs", etag))
        (snapshot / "config.json").symlink_to(Path("..", "..", "blobs", "f" * 40))
    except OSError:
        pytest.skip("this system makes no symbolic links for this user")
    return repo


def _stored(root: Path) -> Path:
    return root / "blobs" / XET_HASH[:2] / XET_HASH


def test_a_file_another_model_links_to_is_kept(tmp_path: Path) -> None:
    a = _repo(tmp_path, "a", "1" * 64, share=True)
    b = _repo(tmp_path, "b", "2" * 64, share=True)
    assert (a / "blobs" / ("1" * 64)).is_symlink(), "the library did not share the file"

    freed = _remove_tree(a)

    assert not a.exists()
    assert freed == OWN
    assert _stored(tmp_path).read_bytes() == WEIGHTS
    assert (b / "snapshots" / COMMIT / "model.safetensors").read_bytes() == WEIGHTS


def test_the_last_model_to_link_to_a_file_frees_it(tmp_path: Path) -> None:
    a = _repo(tmp_path, "a", "1" * 64, share=True)
    b = _repo(tmp_path, "b", "2" * 64, share=True)
    _remove_tree(a)

    freed = _remove_tree(b)

    assert not b.exists()
    assert not _stored(tmp_path).exists()
    assert freed == OWN + len(WEIGHTS)


def test_a_model_downloaded_before_the_shared_store_is_removed_whole(tmp_path: Path) -> None:
    a = _repo(tmp_path, "a", "1" * 64, share=False)

    freed = _remove_tree(a)

    assert not a.exists()
    assert freed == OWN + len(WEIGHTS)
