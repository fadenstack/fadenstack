"""A skill's files reach the model, and file-type triggers see the chat's files.

A skill's references/ and assets/ never reached the model, and the gateway
never told the skills service which files a chat had, so a skill set to come
in on a file type never did.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import ChatAttachment, ExtractionStatus, ProviderType
from llm_port_api.services.gateway import skill_files
from llm_port_api.services.gateway.service import _file_types
from llm_port_api.services.gateway.skills_client import ResolvedSkill, SkillResolveResult, SkillsClient
from llm_port_api.services.registry import service_registry
from llm_port_api.settings import settings
from tests.test_gateway_knowledge_tools import CAN_CALL_TOOLS, _call, _Model, _say, _streamed, _tool_names
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _session, _token

FORMULAS = "SUMIF adds the cells that match a condition."


def _skill(**files_text: str | None) -> ResolvedSkill:
    files = [
        {"path": path, "size": len(text or "x" * 20_000), "kind": "text", "executable": False, "text": text}
        for path, text in files_text.items()
    ]
    return ResolvedSkill(
        skill_id="s1", name="Sheets", slug="sheets", version=4,
        body_markdown="Read references/formulas.md when a formula is involved.",
        priority=50, score=80, content_sha256="ab" * 32, matched_by="explicit",
        files=files or None,
    )


class _Skills:
    """The skills service: resolves one skill, reads its files."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, skill: ResolvedSkill) -> None:
        self.resolved: list[dict[str, Any]] = []
        self.read: list[dict[str, Any]] = []
        service_registry.configure("skills", enabled=True, url="http://skills.local")
        monkeypatch.setattr(settings, "skills_service_token", "t")
        state = self

        async def resolve(self: SkillsClient, **kwargs: Any) -> SkillResolveResult:
            state.resolved.append(kwargs)
            return SkillResolveResult(skills=[skill])

        async def read_file(self: SkillsClient, **kwargs: Any) -> dict[str, Any]:
            state.read.append(kwargs)
            return {"path": kwargs["path"], "size": len(FORMULAS), "kind": "text", "text": FORMULAS}

        async def record(self: SkillsClient, **_: Any) -> None: ...

        monkeypatch.setattr(SkillsClient, "resolve_skills", resolve)
        monkeypatch.setattr(SkillsClient, "read_file", read_file)
        monkeypatch.setattr(SkillsClient, "record_usage", record)


@pytest.fixture(autouse=True)
def _skills_off_after() -> Any:
    yield
    service_registry.configure("skills", enabled=False, url=None)


def _system_text(payload: dict[str, Any]) -> str:
    return "\n".join(m.get("content") or "" for m in payload["messages"] if m.get("role") == "system")


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_a_model_that_calls_tools_reads_a_skill_file(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    skills = _Skills(monkeypatch, _skill(**{"references/formulas.md": None}))
    model = _Model(
        monkeypatch,
        _call(skill_files.NAME, {"skill": "sheets", "path": "references/formulas.md"}),
        _say("Use SUMIF."),
    )

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "messages": [{"role": "user", "content": "sum what matches"}],
    })
    assert r.status_code == 200
    text = _streamed(r.text)[0] if stream else r.json()["choices"][0]["message"]["content"]
    assert text == "Use SUMIF."

    assert skills.resolved[0]["inline_files_chars"] == 0  # it can read them itself
    first = model.sent[0]
    assert skill_files.NAME in _tool_names(first)
    assert "references/formulas.md" in _system_text(first)
    assert skills.read == [{"skill_id": "s1", "version": 4, "path": "references/formulas.md", "tenant_id": "tenant-a"}]
    answer = [m for m in model.sent[1]["messages"] if m.get("role") == "tool"]
    assert json.loads(answer[-1]["content"])["text"] == FORMULAS


@pytest.mark.anyio
async def test_a_model_that_cannot_call_tools_gets_the_small_files_inline(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    skills = _Skills(monkeypatch, _skill(**{"references/formulas.md": FORMULAS, "references/big.md": None}))
    model = _Model(monkeypatch, _say("ok"))

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "hi"}],
    })
    assert skills.resolved[0]["inline_files_chars"] == skill_files.INLINE_CHARS
    sent = model.sent[0]
    assert skill_files.NAME not in _tool_names(sent)  # vLLM would refuse the request
    system = _system_text(sent)
    assert FORMULAS in system
    assert "references/big.md (not included: too long)" in system


@pytest.mark.anyio
async def test_a_skill_without_files_reads_as_before(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    _Skills(monkeypatch, _skill())
    model = _Model(monkeypatch, _say("ok"))
    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "hi"}],
    })
    assert skill_files.NAME not in _tool_names(model.sent[0])
    assert (
        "=== ACTIVE SKILL: Sheets (v4) ===\nRead references/formulas.md when a formula is involved.\n"
        "=== END SKILL ==="
    ) in _system_text(model.sent[0])


@pytest.mark.anyio
async def test_the_chats_file_types_go_to_the_skill_triggers(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    skills = _Skills(monkeypatch, _skill())
    _Model(monkeypatch, _say("ok"))
    sid = await _session(db_session)
    for filename, content_type in (("Q3 Report.XLSX", "application/octet-stream"), ("scan", "application/pdf")):
        db_session.add(ChatAttachment(
            tenant_id="tenant-a", user_id="user-1", session_id=sid, filename=filename,
            content_type=content_type, size_bytes=10, storage_key=f"k/{filename}",
            extraction_status=ExtractionStatus.COMPLETED,
        ))
    await db_session.commit()

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "session_id": str(sid), "messages": [{"role": "user", "content": "sum column B"}],
    })
    assert skills.resolved[0]["file_types"] == ["pdf", "xlsx"]


def test_file_types_come_from_names_then_media_types() -> None:
    assert _file_types([("a.PDF", "x"), ("notes", "text/csv"), ("b.tar.gz", ""), ("c", "")]) == ["csv", "gz", "pdf"]


class _Client:
    def __init__(self, text: str | None) -> None:
        self.text = text

    async def read_file(self, **kwargs: Any) -> dict[str, Any]:
        return {"path": kwargs["path"], "size": 1, "kind": "text" if self.text else "binary", "text": self.text}


@pytest.mark.anyio
async def test_a_long_file_is_read_in_parts() -> None:
    reader = skill_files.SkillFiles(
        _Client("a" * 25_000), [_skill(**{"references/long.md": None})], tenant_id="t",  # type: ignore[arg-type]
    )
    first, error = await reader.run({"skill": "sheets", "path": "references/long.md"})
    assert not error
    first_part = json.loads(first)
    assert len(first_part["text"]) == skill_files.CHUNK_CHARS
    assert first_part["next_offset"] == skill_files.CHUNK_CHARS
    rest = json.loads((await reader.run({"skill": "sheets", "path": "references/long.md", "offset": 20_000}))[0])
    assert len(rest["text"]) == 5_000
    assert "next_offset" not in rest


@pytest.mark.anyio
async def test_only_the_files_of_the_active_skills_can_be_read() -> None:
    reader = skill_files.SkillFiles(
        _Client("x"), [_skill(**{"references/a.md": None})], tenant_id="t",  # type: ignore[arg-type]
    )
    other, error = await reader.run({"skill": "someone-elses", "path": "references/a.md"})
    assert error and "No active skill" in json.loads(other)["error"]
    missing, error = await reader.run({"skill": "sheets", "path": "../../etc/passwd"})
    assert error and "has no file" in json.loads(missing)["error"]
    binary = skill_files.SkillFiles(
        _Client(None), [_skill(**{"assets/logo.png": None})], tenant_id="t",  # type: ignore[arg-type]
    )
    answer, error = await binary.run({"skill": "sheets", "path": "assets/logo.png"})
    assert error and "binary" in json.loads(answer)["error"]
