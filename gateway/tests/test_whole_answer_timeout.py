"""An answer that is not streamed may take as long as a whole answer takes.

It comes back only when the model has written all of it. With LiteLLM's 60 s, a model that thinks first ran
out of time drafting a skill from a chat (and LiteLLM asked again, three times), while the edge and the backend
were still waiting for it.
"""

from __future__ import annotations

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
