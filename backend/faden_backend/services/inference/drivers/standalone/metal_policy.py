"""What vLLM-Metal 0.34 may be asked to do on a Mac (CONTRACTS § C3).

One allowlist, used by the console's suggestions, by the host endpoint and by
the standalone driver, so a setting the Mac cannot run is refused where it is
chosen and again where it is started. Anything not named here is refused;
what is named but unproven is left out until it is certified.
"""

from __future__ import annotations

from typing import Any

#: Parsers tested on a Mac with Qwen3 (``c3/ACCEPTANCE.md``). Others are hidden, not flagged.
CERTIFIED_TOOL_PARSERS = frozenset({"hermes"})
CERTIFIED_REASONING_PARSERS = frozenset({"qwen3"})

#: ``quantization`` values vLLM-Metal can load (AWQ checkpoints; MLX-native weights need none).
QUANTIZATIONS = frozenset({"awq", "mlx"})

#: Engine settings a Mac accepts, as the Host Model form and a spec send them.
ALLOWED_KEYS = frozenset({
    "max_model_len", "max_num_seqs", "max_num_batched_tokens", "gpu_memory_utilization", "dtype", "seed",
    "generation_config", "quantization", "enable_auto_tool_choice", "tool_call_parser", "reasoning_parser",
    "enable_prefix_caching",
})

#: Settings refused with their own sentence (the generic "not available" is not helpful for these).
_REASONS = {
    "thinking_token_budget": "a limit on thinking tokens is not supported by vLLM-Metal",
    "kv_cache_dtype": "a quantized KV cache is not supported by vLLM-Metal",
    "trust_remote_code": "running a repository's own code is not offered on a Mac",
    "tensor_parallel_size": "a Mac runs one copy on one machine",
    "pipeline_parallel_size": "a Mac runs one copy on one machine",
    "data_parallel_size": "a Mac runs one copy on one machine",
    "speculative_config": "speculative decoding is not supported by vLLM-Metal",
}

#: vLLM argument names for ``extra_args`` (C2's allowlist is the same set).
_FLAG = {
    "dtype": "--dtype", "max_num_seqs": "--max-num-seqs", "max_num_batched_tokens": "--max-num-batched-tokens",
    "seed": "--seed", "generation_config": "--generation-config",
}


def check(config: dict[str, Any] | None) -> str | None:
    """A sentence for the first setting a Mac cannot run, or ``None`` when all are fine."""
    for key, value in (config or {}).items():
        if value is None:
            continue
        if key in _REASONS and not (key == "kv_cache_dtype" and value == "auto"):
            return f"{key}: {_REASONS[key]}."
        if key not in ALLOWED_KEYS and key != "kv_cache_dtype":
            return f"{key} is not a setting a Mac can run."
        if key == "quantization" and str(value).lower() not in QUANTIZATIONS:
            kind = "GPTQ is not supported by vLLM-Metal" if "gptq" in str(value).lower() else (
                f"{value} is not supported by vLLM-Metal")
            return f"quantization: {kind}; AWQ and MLX models are."
        if key == "enable_prefix_caching" and value:
            return "enable_prefix_caching: prefix caching stays off on a Mac."
        if key == "tool_call_parser" and value not in CERTIFIED_TOOL_PARSERS:
            return f"tool_call_parser: {value} is not certified on a Mac (certified: {', '.join(sorted(CERTIFIED_TOOL_PARSERS))})."
        if key == "reasoning_parser" and value not in CERTIFIED_REASONING_PARSERS:
            return f"reasoning_parser: {value} is not certified on a Mac (certified: {', '.join(sorted(CERTIFIED_REASONING_PARSERS))})."
    return None


def restrict(suggestion: dict[str, Any]) -> dict[str, Any]:
    """Drop from a suggestion what a Mac cannot run (the console's form never offers it)."""
    config, reasons = suggestion["config"], suggestion["reasons"]
    for key in [k for k in config if check({k: config[k]})]:
        config.pop(key)
        reasons.pop(key, None)
    config["enable_prefix_caching"] = False
    reasons["enable_prefix_caching"] = "mac_prefix_caching_off"
    if config.get("enable_auto_tool_choice") and not config.get("tool_call_parser"):
        config.pop("enable_auto_tool_choice")
        reasons.pop("enable_auto_tool_choice", None)
    return suggestion


def start_fields(config: dict[str, Any] | None) -> dict[str, Any]:
    """What ``start_native_worker`` takes from *config*: ``max_model_len``, ``gpu_memory_utilization``, ``extra_args``."""
    config = config or {}
    extra: list[str] = ["--no-enable-prefix-caching"]
    for key, flag in _FLAG.items():
        if config.get(key) is not None:
            extra += [flag, str(config[key])]
    if config.get("enable_auto_tool_choice") and config.get("tool_call_parser"):
        extra += ["--enable-auto-tool-choice", "--tool-call-parser", str(config["tool_call_parser"])]
    if config.get("reasoning_parser"):
        extra += ["--reasoning-parser", str(config["reasoning_parser"])]
    return {
        "max_model_len": config.get("max_model_len"),
        "gpu_memory_utilization": config.get("gpu_memory_utilization"),
        "extra_args": extra,
    }


def apply_fit(suggestion: dict[str, Any], fit: Any) -> dict[str, Any]:
    """Overwrite the memory settings with the Mac plan's: a recipe's are written for other hardware."""
    if fit is None or getattr(fit, "status", None) != "fits":
        return suggestion
    config, reasons = suggestion["config"], suggestion["reasons"]
    config["gpu_memory_utilization"] = fit.suggested_gpu_memory_utilization
    reasons["gpu_memory_utilization"] = "fit"
    config["max_num_seqs"] = fit.max_num_seqs
    reasons["max_num_seqs"] = "fit"
    if fit.context:
        config["max_model_len"] = fit.context
        reasons["max_model_len"] = "memory" if "context_reduced" in (fit.notes or []) else "default_cap"
    return suggestion


#: Flags the backend sets from the plan and the allowlist; an extension's ``extra_args`` may not repeat them.
_POLICY_FLAGS = frozenset({
    "--gpu-memory-utilization", "--max-model-len", "--max-num-seqs", "--max-num-batched-tokens", "--dtype",
    "--seed", "--generation-config", "--enable-prefix-caching", "--no-enable-prefix-caching",
    "--tool-call-parser", "--reasoning-parser", "--enable-auto-tool-choice", "--kv-cache-dtype", "--quantization",
})


def check_extra_args(args: Any) -> str | None:
    """A sentence when *args* (an extension's ``extra_args``) repeats a flag the plan or the allowlist owns."""
    for arg in args or []:
        flag = str(arg).split("=", 1)[0]
        if flag in _POLICY_FLAGS:
            return (
                f"extra_args: {flag} is set from the memory plan and the Mac allowlist; "
                "change it in the engine settings instead."
            )
    return None
