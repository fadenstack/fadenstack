"""The memory plan for a Mac: one pool of unified memory, and the settings vLLM-Metal can run."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from faden_backend.services.inference.drivers.standalone import admission, metal_policy
from faden_backend.services.marketplace import fit as fit_mod

GiB = 2**30
R64 = 55_662_788_608  # measured on the M5 Pro, Gate 0


def metal(unified_gib: int, *, estimated: bool = False, working_set: int | None = None) -> dict:
    return {
        "working_set_bytes": working_set or int(unified_gib * GiB * 0.81),
        "unified_bytes": unified_gib * GiB, "estimated": estimated,
    }


def qwen06() -> fit_mod.ModelNeeds:  # 28 layers, 8 KV heads x 128 x 2 x bf16 = 114688 B per token
    return fit_mod.ModelNeeds(weights_bytes=int(1.2 * GiB), kv_bytes_per_token=114_688, max_context=40960)


def test_the_reserve_follows_the_ram_band() -> None:
    assert fit_mod.metal_reserve_bytes(36 * GiB) == 3 * GiB
    assert fit_mod.metal_reserve_bytes(64 * GiB) == 4 * GiB
    assert fit_mod.metal_reserve_bytes(128 * GiB) == 6 * GiB


@pytest.mark.parametrize(("unified", "max_share"), [(36, 0.90), (64, 0.93), (128, 0.95)])
def test_the_ceiling_is_the_lower_of_95_percent_and_what_the_reserve_leaves(unified: int, max_share: float) -> None:
    m = metal(unified)
    share = fit_mod.metal_ceiling(m["working_set_bytes"], m["unified_bytes"]) / m["working_set_bytes"]
    assert share <= 0.95 and share == pytest.approx(min(0.95, 1 - fit_mod.metal_reserve_bytes(unified * GiB) / m["working_set_bytes"]), abs=1.5e-4)
    assert share <= max_share + 0.02


def test_a_small_model_fits_with_a_share_of_the_working_set_and_never_above_95() -> None:
    fit = fit_mod.plan_metal(qwen06(), metal(64, working_set=R64))
    assert fit.status == "fits"
    assert fit.suggested_gpu_memory_utilization <= 0.95
    assert fit.max_num_seqs == 8 and fit.context == 32768
    assert fit.metal["working_set_bytes"] == R64 and fit.metal["estimated"] is False
    # the budget is the need plus a tenth, inside the ceiling
    assert fit.metal["budget_bytes"] <= fit.metal["ceiling_bytes"]


def test_the_estimate_flag_is_carried() -> None:
    assert fit_mod.plan_metal(qwen06(), metal(64, estimated=True)).metal["estimated"] is True


def test_a_big_cache_lowers_sequences_then_context() -> None:
    big = fit_mod.ModelNeeds(weights_bytes=20 * GiB, kv_bytes_per_token=400_000, max_context=131072)
    fit = fit_mod.plan_metal(big, metal(36))
    assert fit.status == "fits" and "sequences_reduced" in fit.notes
    assert fit.max_num_seqs < 8 or fit.context < 32768
    assert fit.needed_bytes_per_gpu <= fit.metal["ceiling_bytes"]


def test_a_model_that_does_not_fit_is_refused_with_the_figures() -> None:
    huge = fit_mod.ModelNeeds(weights_bytes=60 * GiB, kv_bytes_per_token=100_000, max_context=8192)
    fit = fit_mod.plan_metal(huge, metal(64))
    assert fit.status == "too_large" and "too_large" in fit.notes
    assert fit.needed_bytes_per_gpu > fit.metal["ceiling_bytes"]


def test_a_small_mac_is_refused() -> None:
    assert fit_mod.plan_metal(qwen06(), metal(16)).notes == ["too_little_memory"]


def test_no_reported_memory_means_no_plan() -> None:
    assert fit_mod.plan_metal(qwen06(), {"working_set_bytes": 0}).status == "no_accelerators"


def test_the_cluster_plan_dispatches_to_the_mac() -> None:
    cluster = fit_mod.ClusterHardware(environment_id="e", name="mac", status="ready", metal=metal(64))
    assert fit_mod.plan(qwen06(), cluster).metal is not None


def test_a_cuda_cluster_is_planned_as_before() -> None:
    cluster = fit_mod.ClusterHardware(
        environment_id="e", name="gpu", status="ready",
        machines=[fit_mod.Machine("n", "n", [fit_mod.Gpu("H100", 80 * GiB, 80 * GiB)])],
    )
    fit = fit_mod.plan(qwen06(), cluster)
    assert fit.status == "fits" and fit.metal is None


# --- the allowlist --------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("config", "words"),
    [
        ({"quantization": "gptq"}, "GPTQ"),
        ({"quantization": "gptq_marlin"}, "GPTQ"),
        ({"quantization": "fp8"}, "not supported"),
        ({"kv_cache_dtype": "fp8"}, "KV cache"),
        ({"thinking_token_budget": 100}, "thinking"),
        ({"enable_prefix_caching": True}, "prefix caching"),
        ({"tool_call_parser": "llama3_json"}, "not certified"),
        ({"reasoning_parser": "deepseek_r1"}, "not certified"),
        ({"tensor_parallel_size": 2}, "one machine"),
        ({"trust_remote_code": True}, "repository"),
        ({"cpu_offload_gb": 4}, "not a setting"),
    ],
)
def test_what_a_mac_cannot_run_is_refused(config: dict, words: str) -> None:
    assert words in (metal_policy.check(config) or "")


def test_what_a_mac_can_run_passes() -> None:
    assert metal_policy.check({
        "quantization": "awq", "kv_cache_dtype": "auto", "enable_prefix_caching": False, "max_model_len": 8192,
        "tool_call_parser": "hermes", "enable_auto_tool_choice": True, "reasoning_parser": "qwen3",
    }) is None


def test_a_suggestion_keeps_only_certified_parsers_and_turns_prefix_caching_off() -> None:
    s = {"config": {"tool_call_parser": "qwen3_coder", "enable_auto_tool_choice": True,
                    "reasoning_parser": "qwen3", "kv_cache_dtype": "fp8"},
         "reasons": {"tool_call_parser": "model_family"}}
    metal_policy.restrict(s)
    assert s["config"] == {"reasoning_parser": "qwen3", "enable_prefix_caching": False}


def test_start_fields_always_turn_prefix_caching_off() -> None:
    f = metal_policy.start_fields({"max_model_len": 4096, "max_num_seqs": 4, "gpu_memory_utilization": 0.5,
                                  "tool_call_parser": "hermes", "enable_auto_tool_choice": True})
    assert f["max_model_len"] == 4096 and f["gpu_memory_utilization"] == 0.5
    assert "--no-enable-prefix-caching" in f["extra_args"] and "--tool-call-parser" in f["extra_args"]


# --- admission ------------------------------------------------------------------------------------

def node(unified: int = 64, **metal_caps: int) -> SimpleNamespace:
    return SimpleNamespace(capabilities_json={"metal": {"unified_memory_bytes": unified * GiB, **metal_caps}})


def test_a_share_above_the_ceiling_is_refused_with_gib() -> None:
    with pytest.raises(ValueError, match=r"at most about \d+\.\d GiB \(estimated\)"):
        admission.memory_budget(node(), None, 0.95)


def test_the_default_share_stays_within_the_ceiling() -> None:
    assert admission.memory_budget(node(36), None)["gpu_memory_utilization"] <= 0.8


def test_a_named_share_within_the_ceiling_is_kept() -> None:
    assert admission.memory_budget(node(), None, 0.5)["gpu_memory_utilization"] == 0.5


# --- plan and admission agree --------------------------------------------------------------------

def test_every_plan_that_fits_is_admitted_and_never_eats_the_reserve() -> None:
    import itertools

    checked = 0
    for unified, weights_gib, kv, ctx, seqs in itertools.product(
        (36, 48, 64, 96, 128, 192), (0.5, 1.2, 5, 14, 20, 30, 45), (50_000, 114_688, 147_456, 400_000),
        (None, 4096, 16384, 32768), (None, 1, 4, 8),
    ):
        m = metal(unified, working_set=R64 if unified == 64 else None)
        needs = fit_mod.ModelNeeds(weights_bytes=int(weights_gib * GiB), kv_bytes_per_token=kv, max_context=131072)
        fit = fit_mod.plan_metal(needs, m, context=ctx, sequences=seqs)
        if fit.status != "fits":
            continue
        checked += 1
        node = SimpleNamespace(capabilities_json={"metal": {
            "unified_memory_bytes": unified * GiB,
            **({"recommended_max_working_set_bytes": R64} if unified == 64 else {}),
        }})
        share = fit.suggested_gpu_memory_utilization
        admitted = admission.memory_budget(node, None, share)["gpu_memory_utilization"]
        assert admitted == pytest.approx(share)
        assert share * m["working_set_bytes"] <= m["working_set_bytes"] - fit_mod.metal_reserve_bytes(unified * GiB) + 1
        # at least what vLLM needs to start: the weights, the overhead and one full-length sequence
        start = needs.weights_bytes + fit_mod.OVERHEAD_BYTES + kv * fit.context
        assert share * m["working_set_bytes"] >= start - 0.0001 * m["working_set_bytes"]
        assert share <= 0.95
    assert checked > 100


def test_the_64_gib_ceiling_is_one_number_for_plan_and_admission() -> None:
    share = fit_mod.metal_ceiling_share(R64, 64 * GiB)
    assert share == pytest.approx(0.9228, abs=1e-4)
    fit = fit_mod.plan_metal(fit_mod.ModelNeeds(weights_bytes=40 * GiB, kv_bytes_per_token=100_000, max_context=8192), metal(64, working_set=R64))
    assert fit.status == "fits" and fit.suggested_gpu_memory_utilization <= share


def test_a_context_above_the_models_own_is_planned_at_its_own() -> None:
    needs = fit_mod.ModelNeeds(weights_bytes=GiB, kv_bytes_per_token=100_000, max_context=8192)
    fit = fit_mod.plan_metal(needs, metal(64), context=999_999)
    assert fit.status == "fits" and fit.context == 8192 and "context_over_model_max" in fit.notes


@pytest.mark.parametrize(
    ("args", "words"),
    [
        (["--gpu_memory_utilization=0.99"], "--gpu_memory_utilization is not allowed"),
        (["--max_model_len", "99999"], "--max_model_len is not allowed"),
        (["--gpu-memory", "0.99"], "--gpu-memory is not allowed"),
        (["--gpu-memory-utilization=0.99"], "--gpu-memory-utilization is not allowed"),
        (["--seed", "--gpu-memory-utilization=0.99"], "--seed '--gpu-memory-utilization=0.99' is not allowed"),
        (["--unknown-flag"], "--unknown-flag is not allowed"),
        (["--seed"], "needs a value"),
        (["--enable-auto-tool-choice=1"], "takes no value"),
        (["--dtype", "float64"], "not allowed"),
        (["--no-enable-prefix-caching"], "set from the memory plan"),
        (["--enable-prefix-caching"], "set from the memory plan"),
    ],
)
def test_extension_extra_args_the_agent_would_refuse_or_the_plan_owns_are_refused(args: list, words: str) -> None:
    assert words in (metal_policy.check_extra_args(args, {}) or "")


def test_a_flag_the_plan_sets_cannot_be_repeated_but_is_free_otherwise() -> None:
    plan = {"max_num_seqs": 4}
    assert "set from the memory plan" in (metal_policy.check_extra_args(["--max-num-seqs", "9"], plan) or "")
    assert metal_policy.check_extra_args(["--max-num-seqs", "9"], {}) is None  # nothing set it


@pytest.mark.parametrize(
    "args",
    [["--dtype", "bfloat16"], ["--dtype=auto"], ["--max-num-seqs", "8"], ["--max-num-batched-tokens=4096"],
     ["--seed", "7"], ["--tool-call-parser", "hermes"], ["--reasoning-parser=qwen3"],
     ["--generation-config", "vllm"], ["--enable-auto-tool-choice"]],
)
def test_each_flag_the_agent_allows_is_accepted_with_a_valid_value(args: list) -> None:
    assert metal_policy.check_extra_args(args, {}) is None


def test_the_backend_list_equals_the_agents() -> None:
    """The two allowlists must stay identical: same flags, same value patterns."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "agent" / "faden_agent" / "native" / "worker.py"
    if not path.exists():
        pytest.skip("the agent source is not in this checkout")
    # Only the one table is evaluated: the module's own imports need the agent's environment.
    import ast
    import re

    tree = ast.parse(path.read_text())
    node = next(
        n for n in tree.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "_EXTRA"
    )
    table = eval(compile(ast.Expression(node.value), "_EXTRA", "eval"), {"re": re})  # noqa: S307
    theirs = {k: (v.pattern if v else None) for k, v in table.items()}  # noqa: SLF001
    ours = {k: (v.pattern if v else None) for k, v in metal_policy.EXTRA_ALLOWED.items()}
    assert ours == theirs
