"""Fail the image build if Ray Serve LLM imports anything this vLLM does not have.

Ray's LLM layer is built against one vLLM, and vLLM's images move on
their own schedule. vLLM 0.29 moved two modules Ray 2.58
imports, and the first image built on it had no way of noticing: it built,
and Ray Serve LLM failed to import on the DGX nodes at deploy time.

So this reads every ``from vllm... import ...`` and ``import vllm...`` under
``ray/llm`` and asserts each module imports and has each name. It needs no GPU.
An import Ray guards itself (inside a ``try``) is optional to Ray and is not
held against the image.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path


def _guarded(tree: ast.AST) -> set[int]:
    """ids of the import nodes inside a ``try`` body: optional to Ray."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for statement in node.body:
                inside.update(id(child) for child in ast.walk(statement))
    return inside


def wanted() -> dict[str, set[str]]:
    """Every vLLM module Ray's LLM layer imports unguarded, with the names it takes."""
    import ray  # noqa: PLC0415

    found: dict[str, set[str]] = {}
    for path in (Path(ray.__file__).parent / "llm").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = _guarded(tree)
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("vllm"):
                found.setdefault(node.module, set()).update(a.name for a in node.names if a.name != "*")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("vllm"):
                        found.setdefault(alias.name, set())
    return found


def main() -> int:
    missing: list[str] = []
    imports = wanted()
    for module, names in sorted(imports.items()):
        try:
            loaded = importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - any failure to import is the finding
            missing.append(f"{module}: {type(exc).__name__}: {exc}")
            continue
        gone = sorted(name for name in names if not hasattr(loaded, name))
        if gone:
            missing.append(f"{module}: no {', '.join(gone)}")
    if missing:
        print("Ray Serve LLM imports what this vLLM does not have:", *missing, sep="\n  ")
        return 1
    print(f"Ray Serve LLM's vLLM imports resolve ({len(imports)} modules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
