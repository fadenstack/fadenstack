"""Temporary: the two vLLM modules Ray 2.58 imports from where vLLM 0.29 no longer has them.

Ray Serve LLM 2.58 imports

    vllm.entrypoints.openai.cli_args          (FrontendArgs)
    vllm.entrypoints.openai.engine.protocol   (ErrorInfo, ErrorResponse)

and vLLM 0.29 keeps them at
``vllm.entrypoints.launchers.cli_args`` and
``vllm.entrypoints.serve.engine.protocol``. Nothing else Ray imports moved
(``check_ray_vllm.py`` asserts that after this runs).

This writes a module at each old path that re-exports the new one, and only
where the old path is missing and the new one exists: on the vLLM Ray was
built against it changes nothing.

Remove it, and its line in the Dockerfile, with the first Ray release that
imports the new paths.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

MOVED = {
    "vllm.entrypoints.openai.cli_args": "vllm.entrypoints.launchers.cli_args",
    "vllm.entrypoints.openai.engine.protocol": "vllm.entrypoints.serve.engine.protocol",
}


def _exists(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:  # a parent package is missing
        return False


def main() -> None:
    import vllm  # noqa: PLC0415

    root = Path(vllm.__file__).parent
    for old, new in MOVED.items():
        if _exists(old) or not _exists(new):
            print(f"vllm_compat: {old} left as it is")
            continue
        path = root.joinpath(*old.split(".")[1:]).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        package = path.parent
        while package != root and not (package / "__init__.py").exists():
            (package / "__init__.py").write_text("", encoding="utf-8")
            package = package.parent
        path.write_text(
            f'"""{old} for Ray 2.58: re-exports {new} (runtime-image/vllm_compat.py)."""\n'
            f"from {new} import *  # noqa: F401,F403\n",
            encoding="utf-8",
        )
        print(f"vllm_compat: {old} -> {new}")


if __name__ == "__main__":
    main()
