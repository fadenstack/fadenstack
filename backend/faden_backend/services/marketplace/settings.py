"""Engine settings suggested for a model, from what it is and where it will run.

Only names the cluster runtime's vLLM actually registers are suggested: the
tool-call and reasoning parser lists below were read from vLLM 0.31.0 in the
DGX runtime image (``ToolParserManager`` / ``ReasoningParserManager``). A
suggestion the engine does not know would fail the deployment at start-up,
which is worse than no suggestion.

Every suggestion carries a reason code, so the interface can say *why* a value
was chosen, and the operator can change any of them.
"""

from __future__ import annotations

import math
import re
from typing import Any

from faden_backend.services.marketplace.fit import (
    DEFAULT_CONTEXT,
    MAX_UTILIZATION,
    Fit,
    ModelNeeds,
    per_gpu_bytes,
)

#: Registered in the runtimes' vLLM 0.31.0 (``vllm.tool_parsers.ToolParserManager``, read from the
#: gb10 runtime image on 13 Nov 2026). Runtimes with an older vLLM register a subset; until each
#: runtime lists its own, these are the names a suggestion may use.
TOOL_PARSERS = frozenset({
    "apertus", "cohere_command3", "cohere_command4", "deepseek_v3", "deepseek_v31", "deepseek_v32",
    "deepseek_v4", "deepseek_v41", "dots", "ernie45", "functiongemma", "gemma4", "gigachat3", "glm45",
    "glm47", "granite", "granite-20b-fc", "granite4", "hermes", "hunyuan_a13b", "hy_v3", "hy_v4",
    "inkling", "internlm", "jamba", "k2_horizon", "kimi_k2", "kimi_k3", "lfm2", "ling3", "llama3_json",
    "llama4_json", "llama4_pythonic", "longcat", "mimo", "minicpm5", "minimax_m2", "minimax_m3",
    "mistral", "muse_glimmer", "olmo3", "openai", "phi4_mini_json", "poolside_v1", "pythonic",
    "qwen3_coder", "qwen3_xml", "seed_oss", "step3", "step3p5", "xlam",
})
REASONING_PARSERS = frozenset({
    "cohere_command3", "cohere_command4", "deepseek_r1", "deepseek_v3", "deepseek_v4", "deepseek_v41",
    "ernie45", "gemma4", "glm45", "glm47", "granite", "granite_thinking_parser", "holo2", "hunyuan_a13b",
    "hy_v3", "hy_v4",
    "inkling", "k2_horizon", "kimi_k2", "kimi_k3", "ling3", "mimo", "minimax_m2", "minimax_m2_append_think",
    "minimax_m3", "mistral", "muse_glimmer", "nemotron_v3", "olmo3", "openai_gptoss", "poolside_v1", "qwen3",
    "seed_oss", "step3", "step3p5",
})

#: (pattern on the repo id, tool parser). First match wins, so specific before general.
_TOOL_RULES: list[tuple[str, str]] = [
    (r"qwen3-coder|qwen3\.\d-coder", "qwen3_coder"),
    # From Qwen3.5 on the family calls tools in XML; vLLM's recipes use this parser.
    (r"qwen3\.[5-9]", "qwen3_xml"),
    (r"qwen|qwq", "hermes"),
    (r"gemma-?4", "gemma4"),
    (r"llama-?4", "llama4_pythonic"),
    (r"llama-?3\.[1-9]|llama-?3-[1-9]", "llama3_json"),
    (r"mistral|magistral|devstral|ministral|codestral", "mistral"),
    (r"gpt-oss", "openai"),
    (r"phi-?4-mini", "phi4_mini_json"),
    (r"deepseek-v3\.2", "deepseek_v32"),
    (r"deepseek-v3\.1", "deepseek_v31"),
    (r"deepseek-v3", "deepseek_v3"),
    (r"glm-4\.7", "glm47"),
    (r"glm-4\.[56]", "glm45"),
    (r"kimi-k2", "kimi_k2"),
    (r"granite-4", "granite4"),
    (r"granite", "granite"),
    (r"hunyuan", "hunyuan_a13b"),
    (r"minimax-m2", "minimax_m2"),
    (r"seed-oss", "seed_oss"),
    (r"olmo-?3", "olmo3"),
    (r"ernie-4\.5", "ernie45"),
    (r"xlam", "xlam"),
    (r"internlm", "internlm"),
]

_REASONING_RULES: list[tuple[str, str]] = [
    (r"qwen3-.*instruct-2507|qwen3-coder|qwen3-embedding|qwen3-vl-.*instruct", ""),  # not thinking models
    (r"qwen3|qwq", "qwen3"),
    (r"deepseek-r1", "deepseek_r1"),
    (r"gpt-oss", "openai_gptoss"),
    (r"gemma-?4", "gemma4"),
    (r"glm-4\.7", "glm47"),
    (r"glm-4\.[56]", "glm45"),
    (r"kimi-k2", "kimi_k2"),
    (r"magistral", "mistral"),
    (r"minimax-m2", "minimax_m2"),
    (r"seed-oss", "seed_oss"),
    (r"nemotron.*v3|nemotron-3", "nemotron_v3"),
    (r"ernie-4\.5.*thinking", "ernie45"),
]

#: The longest context suggested by default. Longer is available -- it is a
#: slider -- but a default that reserves memory for 128K tokens nobody asked
#: for halves the number of conversations a copy can hold.
DEFAULT_CONTEXT_CAP = 32_768


def _first(rules: list[tuple[str, str]], repo_id: str) -> str | None:
    name = repo_id.lower()
    for pattern, value in rules:
        if re.search(pattern, name):
            return value or None
    return None


def tool_parser_for(repo_id: str) -> str | None:
    parser = _first(_TOOL_RULES, repo_id)
    return parser if parser in TOOL_PARSERS else None


def reasoning_parser_for(repo_id: str) -> str | None:
    parser = _first(_REASONING_RULES, repo_id)
    return parser if parser in REASONING_PARSERS else None


def suggest(model: dict[str, Any], fit: Fit | None) -> dict[str, Any]:
    """``{"config": engine settings, "reasons": {setting: why}}`` for *model* on a cluster.

    *model* is a marketplace card or detail; *fit* the plan for the chosen
    cluster (``None`` when there is none).
    """
    repo_id = str(model.get("repo_id") or "")
    caps = set(model.get("capabilities") or [])
    config: dict[str, Any] = {}
    reasons: dict[str, str] = {}

    if "images" in caps:
        # A picture model takes picture settings, none of vLLM's: its own
        # defaults unless its family is known to want others (curated).
        return {"config": config, "reasons": reasons}

    if "embedding" in caps:
        config["runner"] = "pooling"
        reasons["runner"] = "embedding_model"
        architecture = str(model.get("architecture") or "")
        if architecture.endswith("ForCausalLM"):
            # A chat architecture used for embeddings (Qwen3-Embedding) is
            # adapted by vLLM rather than served as a generator.
            config["convert"] = "embed"
            reasons["convert"] = "embedding_from_causal_lm"
    else:
        parser = tool_parser_for(repo_id)
        if parser and ("tools" in caps or parser in {"openai", "mistral", "llama3_json", "qwen3_coder"}):
            config["enable_auto_tool_choice"] = True
            config["tool_call_parser"] = parser
            reasons["tool_call_parser"] = "model_family"
        reasoning = reasoning_parser_for(repo_id)
        if reasoning:
            config["reasoning_parser"] = reasoning
            reasons["reasoning_parser"] = "model_family"

    max_context = model.get("max_context")
    fits_context = fit.max_context if fit is not None else None
    ceiling = min(v for v in (max_context, fits_context) if v) if (max_context or fits_context) else None
    if ceiling:
        cap = ceiling if "embedding" in caps else min(ceiling, DEFAULT_CONTEXT_CAP)
        chosen = max(1024, cap // 1024 * 1024) if cap >= 1024 else cap
        if not max_context or chosen < max_context:
            config["max_model_len"] = chosen
            reasons["max_model_len"] = "memory" if fits_context and fits_context < (max_context or 0) \
                and fits_context <= DEFAULT_CONTEXT_CAP else "default_cap"

    if fit is not None and fit.suggested_gpu_memory_utilization is not None:
        config["gpu_memory_utilization"] = fit.suggested_gpu_memory_utilization
        reasons["gpu_memory_utilization"] = "shared_gpu" if fit.shareable else "fit"

    if fit is not None and fit.max_num_seqs:
        # A hybrid model: vLLM's default of 256 parallel sequences can need
        # more recurrent state than the copy has, and then it does not start.
        config["max_num_seqs"] = fit.max_num_seqs
        reasons["max_num_seqs"] = "recurrent_state"

    if model.get("needs_remote_code"):
        # Suggested, never applied silently: it runs the repository's own code.
        reasons["trust_remote_code"] = "needs_remote_code"

    return {"config": config, "reasons": reasons}


def _share_up(value: float, step: float = 0.05) -> float:
    return round(math.ceil(value / step - 1e-9) * step, 2)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def reconcile(suggestion: dict[str, Any], fit: Fit | None, needs: ModelNeeds | None = None) -> dict[str, Any]:
    """Check the values a recipe set against the fit, and drop what cannot stand alone.

    A recipe's settings go in after the family rules, and they used to
    replace the fit's memory values unchecked: a recipe's context or memory
    share is written for its own hardware, not this cluster. Only values whose
    reason is ``recipe`` are checked; the family rules come from the fit
    already, and the curated ones were tested. A value the copy cannot hold is
    brought back to the fit's, with the fit's reason, so the dialog says why.
    """
    config, reasons = suggestion["config"], suggestion["reasons"]
    # vLLM refuses automatic tool choice without a parser to read the calls.
    if config.get("enable_auto_tool_choice") and not config.get("tool_call_parser"):
        config.pop("enable_auto_tool_choice")
        reasons.pop("enable_auto_tool_choice", None)
    if fit is None or fit.status != "fits":
        return suggestion

    def from_recipe(key: str) -> bool:
        return reasons.get(key) == "recipe" and _number(config.get(key))

    if from_recipe("max_model_len"):
        _check_context(config, reasons, fit)
    if from_recipe("gpu_memory_utilization"):
        _check_share(config, reasons, fit)
    if from_recipe("max_num_seqs") and needs is not None:
        _check_sequences(config, reasons, fit, needs)
    return suggestion


def _check_context(config: dict[str, Any], reasons: dict[str, str], fit: Fit) -> None:
    """A context the copy cannot hold comes back to what it holds."""
    held = fit.max_context
    if held and config["max_model_len"] > held:
        config["max_model_len"] = max(1024, held // 1024 * 1024) if held >= 1024 else held
        reasons["max_model_len"] = "memory"


def _check_share(config: dict[str, Any], reasons: dict[str, str], fit: Fit) -> None:
    """The memory share: no more than the copy's share or the ceiling, no less than the model needs."""
    share = float(config["gpu_memory_utilization"])
    planned = fit.suggested_gpu_memory_utilization
    floor = None
    if fit.needed_bytes_per_gpu and fit.gpu_bytes:
        floor = _share_up(fit.needed_bytes_per_gpu / fit.gpu_bytes)
    if fit.shareable and planned is not None and share > planned:
        # The copy shares its accelerator: more would crowd the others on it.
        config["gpu_memory_utilization"] = planned
        reasons["gpu_memory_utilization"] = "shared_gpu"
    elif share > MAX_UTILIZATION:
        config["gpu_memory_utilization"] = MAX_UTILIZATION
        reasons["gpu_memory_utilization"] = "fit"
    elif floor is not None and share < floor:
        # Less than the weights and the context need: vLLM would not start.
        config["gpu_memory_utilization"] = min(floor, MAX_UTILIZATION)
        reasons["gpu_memory_utilization"] = "fit"


def _check_sequences(config: dict[str, Any], reasons: dict[str, str], fit: Fit, needs: ModelNeeds) -> None:
    """A hybrid model keeps a recurrent state per sequence before it starts at all."""
    sequences = int(config["max_num_seqs"])
    if not (fit.max_num_seqs and needs.state_bytes_per_sequence and fit.gpu_bytes) or sequences <= fit.max_num_seqs:
        return
    share = config.get("gpu_memory_utilization")
    budget = (float(share) if _number(share) else MAX_UTILIZATION) * fit.gpu_bytes
    needed = per_gpu_bytes(
        needs, tp=fit.tensor_parallel or 1, context=fit.context or DEFAULT_CONTEXT,
        kv_bytes_per_token=fit.kv_bytes_per_token, sequences=sequences,
    )
    if needed > budget:
        config["max_num_seqs"] = fit.max_num_seqs
        reasons["max_num_seqs"] = "recurrent_state"
