"""Which engine settings of a deployment only a superuser may set.

``engine.config`` reaches vLLM as keyword arguments on the GPU machines
(``drivers/ray/compiler.py``), and operators -- a role an administrator can hand
to anyone -- may write it. Most of vLLM's arguments tune performance. Some do
more than that:

* they run code the model repository or the setting brings along
  (``trust_remote_code``), or import a named class (``scheduler_cls``,
  ``logits_processors``, the KV connectors ...);
* they read or write files on the machine outside the model store
  (``allowed_local_media_path``, ``tokenizer``, ``chat_template`` as a path ...);
* they send traffic or credentials somewhere else (``otlp_traces_endpoint``,
  ``hf_token``).

An operator may not set those. A superuser may, and the console says what each
one means before it lets them. An operator who edits a deployment a superuser
set up keeps the settings that were already there: the console sends the whole
spec back, and refusing the unchanged part would make the deployment
uneditable.

This runs where the deployment is saved (``DeploymentService``), because the
reconciler that compiles it has no user. It does not run at compile time: a
deployment saved before this check keeps running as it was.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterable, Mapping
from typing import Any

from faden_backend.settings import settings

#: Settings that run code, import a class, or hand the engine a credential, with
#: what each one does. Superuser only, whatever the value.
CODE_SETTINGS: dict[str, str] = {
    "trust_remote_code": "runs the Python code that ships inside the model's repository on the GPU machines",
    "scheduler_cls": "imports and runs a named Python class as the engine's scheduler",
    "logits_processors": "imports and runs named Python classes on every token",
    "io_processor_plugin": "loads a named plugin into the engine",
    "tool_parser_plugin": "loads a Python file as a tool-call parser",
    "reasoning_parser_plugin": "loads a Python file as a reasoning parser",
    "worker_cls": "imports and runs a named Python class as the engine's worker",
    "worker_extension_cls": "imports a named Python class into every worker",
    "distributed_executor_backend": "can name a Python class to run the engine's workers",
    "model_class_overrides": "replaces the class that implements the model",
    "kv_transfer_config": "loads a KV-cache connector, which can be an imported module",
    "ec_transfer_config": "loads an encoder-cache connector, which can be an imported module",
    "kv_events_config": "publishes KV-cache events to an address",
    "weight_transfer_config": "loads weights through a connector",
    "model_loader_extra_config": "hands the weight loader URIs and options of its own",
    "additional_config": "passes free-form options to platform plugins",
    "hf_token": "puts a Hugging Face credential into the stored deployment",
    "otlp_traces_endpoint": "sends request traces to an address",
    "ssl_keyfile": "reads a private key from a file on the machine",
    "ssl_certfile": "reads a certificate from a file on the machine",
    "ssl_ca_certs": "reads certificates from a file on the machine",
    "allowed_local_media_path": "lets requests read files from a directory on the machine",
    "watermark_config": (
        "switches off the text watermark that marks this model's answers as AI-generated "
        "(EU AI Act Art. 50(2)); the installation's own key is used otherwise, never one in the deployment"
    ),
}

#: Settings that take a model reference or a file. A repository id is fine for an
#: operator; a path or a URL is only fine inside the model store.
PATH_SETTINGS: dict[str, str] = {
    "model": "the model to load",
    "tokenizer": "the tokenizer to load",
    "hf_config_path": "the model configuration to load",
    "download_dir": "the directory weights are loaded from",
    "generation_config": "the generation configuration folder",
    "chat_template": "the chat template file",
    "lora_modules": "adapter files",
    "mm_encoder_fp8_scale_path": "a scale file to read",
    "mm_encoder_fp8_scale_save_path": "a file to write",
    "speculative_model": "the draft model to load",
    "qlora_adapter_name_or_path": "an adapter to load",
}

#: Settings whose value is a JSON object: their nested keys and strings are
#: looked at as well.
_NESTED = frozenset({
    "attention_config", "compilation_config", "default_chat_template_kwargs", "default_mm_loras",
    "diffusion_config", "fault_tolerance_config", "hf_overrides", "kernel_config", "limit_mm_per_prompt",
    "media_io_kwargs", "mm_processor_kwargs", "override_generation_config", "pooler_config",
    "quantization_config", "reasoning_config", "speculative_config", "structured_outputs_config",
})

#: A nested key that names code to import or a file to read, whatever it is nested in.
#: (``auto_map`` is how a model's configuration points at repository code.)
_NESTED_CODE_KEY = re.compile(r"(_module_path|_module|_cls|_class|_plugin|_script)$|^auto_map$")

_LOCAL_PATH_START = re.compile(r"^(/|\\|~|\.|[A-Za-z]:)")

#: Where a model lives on a machine: the server's store, and where the runtime
#: container mounts it.
_CONTAINER_MODEL_STORE = "/models"


def _norm(key: str) -> str:
    return key.strip().lstrip("-").replace("-", "_")


def is_path_like(value: Any) -> bool:
    """Whether *value* names a file or a URL rather than a hub repository id."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(text) and (bool(_LOCAL_PATH_START.match(text)) or "://" in text or "\\" in text or ".." in text.split("/"))


def store_roots() -> list[str]:
    roots = [posixpath.normpath(str(settings.model_store_root).replace("\\", "/")), _CONTAINER_MODEL_STORE]
    return [r for r in roots if r and r != "."]


def in_model_store(value: str) -> bool:
    """Whether *value* is a path inside the model store (no ``..``, no URL)."""
    text = value.strip().replace("\\", "/")
    if "://" in text or ".." in text.split("/") or text.startswith("~"):
        return False
    path = posixpath.normpath(text)
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in store_roots())


def _as_object(value: Any) -> Any:
    """A JSON setting kept as text, read into the object it holds (text that is not JSON stays text)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _walk(value: Any) -> Iterable[tuple[str | None, Any]]:
    """Every (key, leaf) of a nested value; the key is ``None`` for a list item."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, (Mapping, list)):
                yield str(key), item  # the container itself, for key checks
                yield from _walk(item)
            else:
                yield str(key), item
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (Mapping, list)):
                yield from _walk(item)
            else:
                yield None, item


def problems(key: str, value: Any) -> list[str]:
    """What is wrong with one engine setting for an operator, as sentences; empty when nothing."""
    name = _norm(key)
    out: list[str] = []
    if name in CODE_SETTINGS:
        if name == "trust_remote_code" and value in (False, None, "false", "False", 0):
            return []  # switching it off is always fine
        out.append(f"{name} {CODE_SETTINGS[name]}")
    elif name in PATH_SETTINGS:
        leaves = [leaf for _, leaf in _walk(_as_object(value))] if isinstance(_as_object(value), (Mapping, list)) else [value]
        for leaf in leaves:
            if name == "chat_template" and isinstance(leaf, str) and ("\n" in leaf or "{%" in leaf or "{{" in leaf):
                continue  # a template written out, not a file
            if is_path_like(leaf) and not in_model_store(str(leaf)):
                out.append(f"{name} ({PATH_SETTINGS[name]}) is {leaf!r}, which is outside the model store")
    elif name in _NESTED:
        for nested_key, leaf in _walk(_as_object(value)):
            if nested_key and _NESTED_CODE_KEY.search(nested_key):
                out.append(f"{name}.{nested_key} names code to load or a file to read")
            elif isinstance(leaf, str) and is_path_like(leaf) and not in_model_store(leaf):
                out.append(f"{name} refers to {leaf!r}, which is outside the model store")
    return out


def guarded_settings(config: Mapping[str, Any] | None, previous: Mapping[str, Any] | None = None) -> dict[str, list[str]]:
    """The settings of *config* an operator may not set, with why; ones unchanged from *previous* pass."""
    previous = {_norm(k): v for k, v in (previous or {}).items()}
    found: dict[str, list[str]] = {}
    for key, value in (config or {}).items():
        why = problems(key, value)
        if why and not (_norm(key) in previous and previous[_norm(key)] == value):
            found[_norm(key)] = why
    return found


def artifact_problems(artifacts: Mapping[str, Any] | None) -> list[str]:
    """``artifacts.root_path`` outside the model store, for an operator."""
    path = (artifacts or {}).get("root_path")
    if is_path_like(path) and not in_model_store(str(path)):
        return [f"artifacts.root_path is {path!r}, which is outside the model store"]
    return []


class EngineSettingsRefused(Exception):
    """The deployment sets something only a superuser may; the message says what."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__(
            "Only an administrator (superuser) may set this: " + "; ".join(reasons) + "."
        )


def check_spec(spec: Mapping[str, Any], *, previous: Mapping[str, Any] | None = None) -> None:
    """Raise :class:`EngineSettingsRefused` when *spec* sets what an operator may not.

    *previous* is the spec the deployment has now (``None`` for a new one):
    whatever it already holds stays.
    """
    reasons: list[str] = []
    engine = (spec.get("engine") or {}).get("config") or {}
    before = ((previous or {}).get("engine") or {}).get("config") or {}
    for key, why in guarded_settings(engine, before).items():
        reasons.extend(f"engine.config.{item}" for item in why)
    artifacts = spec.get("artifacts") or {}
    if (artifacts.get("root_path") != ((previous or {}).get("artifacts") or {}).get("root_path")):
        reasons.extend(artifact_problems(artifacts))
    if reasons:
        raise EngineSettingsRefused(reasons)
