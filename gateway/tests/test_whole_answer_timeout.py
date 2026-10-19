"""An answer that is not streamed may take as long as a whole answer takes.

It comes back only when the model has written all of it. With LiteLLM's 60 s, a model that thinks first ran
out of time drafting a skill from a chat (and LiteLLM asked again, three times), while the edge and the backend
were still waiting for it.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import litellm
import pytest

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway import llm_adapter
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from faden_gateway.settings import settings

MESSAGES = [{"role": "user", "content": "Write a skill from this chat."}]


@pytest.fixture(autouse=True)
def fresh_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """No model is remembered as refusing a system message after the first."""
    monkeypatch.setattr(llm_adapter, "_SYSTEM_FIRST", set())


@pytest.mark.anyio
async def test_a_whole_answer_gets_the_whole_answer_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    async def acompletion(**kwargs: Any) -> Any:
        sent.append(kwargs)
        return SimpleNamespace(model_dump=lambda: {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    result = await LLMAdapter().completion(
        provider_type=ProviderType.REMOTE_OPENAI, base_url="http://api.local/v1", api_key_encrypted=None,
        litellm_provider=None, litellm_model=None, extra_params=None,
        payload={"model": "qwen", "messages": MESSAGES}, stream=False,
    )
    assert result.status_code == 200
    [kwargs] = sent
    assert kwargs["timeout"] == settings.non_stream_timeout_sec
    assert settings.non_stream_timeout_sec > settings.litellm_request_timeout


@pytest.mark.anyio
async def test_a_whole_answer_past_the_wait_is_a_504_that_says_to_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    async def acompletion(**kwargs: Any) -> Any:
        raise litellm.exceptions.Timeout(message="timed out", model="qwen", llm_provider="openai")

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    result = await LLMAdapter().completion(
        provider_type=ProviderType.REMOTE_OPENAI, base_url="http://api.local/v1", api_key_encrypted=None,
        litellm_provider=None, litellm_model=None, extra_params=None,
        payload={"model": "qwen", "messages": MESSAGES}, stream=False,
    )
    assert result.status_code == 504
    error = result.payload["error"]
    assert error["code"] == "upstream_timeout" and "stream" in error["message"]


def test_the_edge_waits_longer_for_v1_than_the_gateway_for_a_whole_answer() -> None:
    """A thinking turn of the 27B (about 8 tokens a second) ran past 300 s, and the client got nginx's 504."""
    conf = Path(__file__).resolve().parents[2] / "shared" / "nginx" / "locations.conf"
    if not conf.exists():
        pytest.skip("the shared nginx configuration is not beside the gateway here")
    block = re.search(r"location /v1/ \{(.*?)\n\}", conf.read_text(encoding="utf-8"), re.DOTALL)
    assert block is not None
    timeout = re.search(r"proxy_read_timeout (\d+)s;", block.group(1))
    assert timeout is not None
    assert settings.non_stream_timeout_sec >= 1200
    assert int(timeout.group(1)) > settings.non_stream_timeout_sec
