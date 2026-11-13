"""The skills API against a real database: who sees what, versions, sharing."""

from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from faden_skills.settings import settings
from tests.conftest import ADMIN, SERVICE_TOKEN, as_user

ALICE = as_user("alice", groups=("g-eng",))
BOB = as_user("bob", groups=("g-sales",))
CAROL = as_user("carol", groups=("g-eng",))
PUBLISHER = as_user("pat", caps=("create", "share", "publish"))


async def _create(
    client: AsyncClient,
    headers: dict[str, str],
    **fields: Any,
) -> dict[str, Any]:
    body = {
        "name": "Code Review",
        "description": "Reviews code. Use when asked to review a diff.",
        "body_markdown": "## Goal\nFind bugs.",
        **fields,
    }
    resp = await client.post("/api/skills", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _resolve(
    client: AsyncClient,
    user: str,
    groups: list[str] | None = None,
    **body: Any,
) -> list[str]:
    resp = await client.post(
        "/api/internal/skills/resolve",
        json={
            "tenant_id": "default",
            "user_id": user,
            "group_ids": groups or [],
            **body,
        },
    )
    assert resp.status_code == 200, resp.text
    return [s["slug"] for s in resp.json()["skills"]]


# ── Ownership and visibility ──────────────────────────────────────────


async def test_a_new_skill_belongs_to_its_creator_and_only_they_see_it(
    client: AsyncClient,
) -> None:
    skill = await _create(client, ALICE)
    assert skill["owner_id"] == "alice"
    assert skill["created_by"] == "alice"
    assert skill["visibility"] == "private"
    assert skill["access"] == "owner"
    assert skill["slug"] == "code-review"

    assert (
        await client.get(f"/api/skills/{skill['id']}", headers=BOB)
    ).status_code == 404
    listed = (await client.get("/api/skills", headers=BOB)).json()
    assert listed["total"] == 0
    admin_view = (await client.get(f"/api/skills/{skill['id']}", headers=ADMIN)).json()
    assert admin_view["access"] == "admin"


async def test_someone_elses_private_skill_never_reaches_your_chat(
    client: AsyncClient,
) -> None:
    """The resolver ignored the user: a private skill matched everyone's chats."""
    await _create(
        client,
        ALICE,
        manifest={"trigger_rules": {"keywords": ["diff"]}},
        publish=True,
    )
    assert await _resolve(client, "alice", user_query="review this diff") == [
        "code-review",
    ]
    assert await _resolve(client, "bob", user_query="review this diff") == []
    assert (
        await _resolve(
            client,
            "bob",
            user_query="use $code-review",
            requested=["code-review"],
        )
        == []
    )


async def test_the_headers_count_only_next_to_the_service_token(
    client: AsyncClient,
) -> None:
    skill = await _create(client, ALICE)
    forged = {**ALICE, "Authorization": "Bearer not-the-token"}
    resp = await client.get(f"/api/skills/{skill['id']}", headers=forged)
    assert resp.status_code == 401
    resp = await client.get(
        f"/api/skills/{skill['id']}",
        headers={**ALICE, "Authorization": f"Bearer {SERVICE_TOKEN}"},
    )
    assert resp.status_code == 200


async def test_without_the_create_capability_nobody_creates(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/api/skills",
        json={"name": "x", "body_markdown": "b"},
        headers=as_user("viewer", caps=()),
    )
    assert resp.status_code == 403


async def test_publishing_to_the_whole_tenant_needs_the_publish_capability(
    client: AsyncClient,
) -> None:
    skill = await _create(client, ALICE, publish=True)
    resp = await client.put(
        f"/api/skills/{skill['id']}/visibility",
        json={"visibility": "tenant"},
        headers=ALICE,
    )
    assert resp.status_code == 403

    mine = await _create(client, PUBLISHER, name="Tone", publish=True)
    resp = await client.put(
        f"/api/skills/{mine['id']}/visibility",
        json={"visibility": "tenant"},
        headers=PUBLISHER,
    )
    assert resp.status_code == 200
    assert await _resolve(client, "bob", user_query="$tone") == ["tone"]


async def test_an_always_on_skill_for_everyone_is_for_administrators(
    client: AsyncClient,
) -> None:
    skill = await _create(client, PUBLISHER, activation="always", publish=True)
    resp = await client.put(
        f"/api/skills/{skill['id']}/visibility",
        json={"visibility": "tenant"},
        headers=PUBLISHER,
    )
    assert resp.status_code == 403
    assert "always-on" in resp.json()["detail"]

    org = await _create(
        client,
        ADMIN,
        name="House Style",
        owner="organization",
        visibility="tenant",
        activation="always",
        publish=True,
    )
    assert org["owner_id"] is None
    assert await _resolve(client, "bob", user_query="hello") == ["house-style"]


# ── Sharing ───────────────────────────────────────────────────────────


async def test_a_skill_shared_with_a_group_reaches_its_members_only(
    client: AsyncClient,
) -> None:
    skill = await _create(client, ALICE, activation="manual", publish=True)
    resp = await client.put(
        f"/api/skills/{skill['id']}/grants",
        json={"grants": [{"principal_type": "group", "principal_id": "g-eng"}]},
        headers=ALICE,
    )
    assert resp.status_code == 200, resp.text

    carol_view = await client.get(f"/api/skills/{skill['id']}", headers=CAROL)
    assert carol_view.json()["access"] == "read"
    assert carol_view.json()["grants"] is None  # only the owner sees who has it
    assert (
        await client.get(f"/api/skills/{skill['id']}", headers=BOB)
    ).status_code == 404

    assert await _resolve(client, "carol", ["g-eng"], requested=["code-review"]) == [
        "code-review",
    ]
    assert await _resolve(client, "bob", ["g-sales"], requested=["code-review"]) == []

    shared = (await client.get("/api/skills?view=shared", headers=CAROL)).json()
    assert [s["slug"] for s in shared["items"]] == ["code-review"]
    mine = (await client.get("/api/skills?view=mine", headers=CAROL)).json()
    assert mine["total"] == 0


async def test_sharing_needs_the_share_capability(client: AsyncClient) -> None:
    owner = as_user("olive", caps=("create",))
    skill = await _create(client, owner)
    resp = await client.put(
        f"/api/skills/{skill['id']}/grants",
        json={"grants": [{"principal_type": "user", "principal_id": "bob"}]},
        headers=owner,
    )
    assert resp.status_code == 403


async def test_readers_may_not_edit_and_writers_may(client: AsyncClient) -> None:
    skill = await _create(client, ALICE)
    await client.put(
        f"/api/skills/{skill['id']}/grants",
        json={
            "grants": [
                {"principal_type": "user", "principal_id": "bob", "access": "read"},
                {"principal_type": "user", "principal_id": "carol", "access": "write"},
            ],
        },
        headers=ALICE,
    )
    change = {"body_markdown": "## Goal\nFind more bugs."}
    resp = await client.post(
        f"/api/skills/{skill['id']}/versions",
        json=change,
        headers=BOB,
    )
    assert resp.status_code == 403
    resp = await client.post(
        f"/api/skills/{skill['id']}/versions",
        json=change,
        headers=CAROL,
    )
    assert resp.status_code == 200
    assert resp.json()["skill"]["latest_version"] == 2
    # A writer may not re-share it or change who sees it.
    resp = await client.put(
        f"/api/skills/{skill['id']}/grants",
        json={"grants": []},
        headers=CAROL,
    )
    assert resp.status_code == 403


async def test_a_deleted_group_loses_its_grants(client: AsyncClient) -> None:
    skill = await _create(client, ALICE, publish=True)
    await client.put(
        f"/api/skills/{skill['id']}/grants",
        json={"grants": [{"principal_type": "group", "principal_id": "g-eng"}]},
        headers=ALICE,
    )
    resp = await client.delete("/api/internal/principals/group/g-eng")
    assert resp.json() == {"removed": 1}
    assert (
        await client.get(f"/api/skills/{skill['id']}", headers=CAROL)
    ).status_code == 404


# ── Versions ──────────────────────────────────────────────────────────


async def test_saving_adds_a_version_and_publishing_moves_the_pointer(
    client: AsyncClient,
) -> None:
    skill = await _create(client, ALICE, activation="manual", publish=True)
    sid = skill["id"]
    resp = await client.post(
        f"/api/skills/{sid}/versions",
        json={"body_markdown": "## Goal\nv2 text", "change_note": "second"},
        headers=ALICE,
    )
    assert resp.status_code == 200
    saved = resp.json()
    assert saved["created"] is True
    assert saved["skill"]["latest_version"] == 2
    assert saved["skill"]["published_version"] == 1

    async def served() -> str:
        resp = await client.post(
            "/api/internal/skills/resolve",
            json={
                "tenant_id": "default",
                "user_id": "alice",
                "requested": ["code-review"],
            },
        )
        [only] = resp.json()["skills"]
        return f"v{only['version']}:{only['body_markdown']}"

    # An edit does not go live until it is published.
    assert await served() == "v1:## Goal\nFind bugs."
    await client.post(f"/api/skills/{sid}/publish", json={}, headers=ALICE)
    assert await served() == "v2:## Goal\nv2 text"
    # Rolling back is publishing an older version.
    await client.post(f"/api/skills/{sid}/publish", json={"version": 1}, headers=ALICE)
    assert await served() == "v1:## Goal\nFind bugs."

    history = (await client.get(f"/api/skills/{sid}/versions", headers=ALICE)).json()
    assert [v["version"] for v in history] == [2, 1]
    assert all(len(v["content_sha256"]) == 64 for v in history)
    diff = (await client.get(f"/api/skills/{sid}/diff?from=1&to=2", headers=ALICE)).text
    assert "-Find bugs." in diff
    assert "+v2 text" in diff


async def test_no_version_is_ever_deleted(client: AsyncClient) -> None:
    """Only five were kept; the audit log named versions that were gone."""
    skill = await _create(client, ALICE)
    for i in range(8):
        resp = await client.post(
            f"/api/skills/{skill['id']}/versions",
            json={"body_markdown": f"text {i}"},
            headers=ALICE,
        )
        assert resp.status_code == 200
    history = (
        await client.get(f"/api/skills/{skill['id']}/versions", headers=ALICE)
    ).json()
    assert [v["version"] for v in history] == list(range(9, 0, -1))
    first = await client.get(f"/api/skills/{skill['id']}/versions/1", headers=ALICE)
    assert first.json()["body_markdown"] == "## Goal\nFind bugs."


async def test_saving_the_same_content_adds_no_version(client: AsyncClient) -> None:
    skill = await _create(client, ALICE)
    resp = await client.post(
        f"/api/skills/{skill['id']}/versions",
        json={"body_markdown": "## Goal\nFind bugs."},
        headers=ALICE,
    )
    assert resp.json()["created"] is False
    assert resp.json()["skill"]["latest_version"] == 1


async def test_a_save_on_top_of_someone_elses_is_refused(client: AsyncClient) -> None:
    skill = await _create(client, ALICE)
    first = await client.post(
        f"/api/skills/{skill['id']}/versions",
        json={"body_markdown": "Alice's edit", "base_version": 1},
        headers=ALICE,
    )
    assert first.status_code == 200
    stale = await client.post(
        f"/api/skills/{skill['id']}/versions",
        json={"body_markdown": "An edit of v1", "base_version": 1},
        headers=ALICE,
    )
    assert stale.status_code == 409
    assert "v2" in stale.json()["detail"]


async def test_readers_see_the_published_version_not_the_draft(
    client: AsyncClient,
) -> None:
    skill = await _create(client, ALICE, publish=True)
    await client.put(
        f"/api/skills/{skill['id']}/grants",
        json={"grants": [{"principal_type": "user", "principal_id": "bob"}]},
        headers=ALICE,
    )
    await client.post(
        f"/api/skills/{skill['id']}/versions",
        json={"body_markdown": "Work in progress"},
        headers=ALICE,
    )
    seen = (await client.get(f"/api/skills/{skill['id']}", headers=BOB)).json()
    assert seen["version"] == 1
    assert seen["body_markdown"] == "## Goal\nFind bugs."
    assert (
        await client.get(f"/api/skills/{skill['id']}/versions/2", headers=BOB)
    ).status_code == 403


async def test_tool_lists_are_versioned_so_a_rollback_restores_them(
    client: AsyncClient,
) -> None:
    skill = await _create(
        client,
        ALICE,
        activation="manual",
        manifest={"forbidden_tools": ["shell.exec"]},
        publish=True,
    )
    await client.post(
        f"/api/skills/{skill['id']}/versions",
        json={"manifest": {}, "publish": True},
        headers=ALICE,
    )

    async def forbidden() -> Any:
        resp = await client.post(
            "/api/internal/skills/resolve",
            json={
                "tenant_id": "default",
                "user_id": "alice",
                "requested": ["code-review"],
            },
        )
        return resp.json()["skills"][0]["forbidden_tools"]

    assert await forbidden() is None
    await client.post(
        f"/api/skills/{skill['id']}/publish",
        json={"version": 1},
        headers=ALICE,
    )
    assert await forbidden() == ["shell.exec"]


# ── Names and limits ──────────────────────────────────────────────────


async def test_two_people_can_each_have_a_skill_with_one_name(
    client: AsyncClient,
) -> None:
    await _create(client, ALICE)
    await _create(client, BOB)
    resp = await client.post(
        "/api/skills",
        json={"name": "Code Review", "body_markdown": "x"},
        headers=ALICE,
    )
    assert resp.status_code == 409


async def test_the_organization_cannot_have_two_skills_with_one_name(
    client: AsyncClient,
) -> None:
    await _create(client, ADMIN, owner="organization", visibility="tenant")
    mine = await _create(client, PUBLISHER)
    resp = await client.put(
        f"/api/skills/{mine['id']}/visibility",
        json={"visibility": "tenant"},
        headers=PUBLISHER,
    )
    assert resp.status_code == 409


async def test_private_skills_do_not_count_against_the_published_limit(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_published_skills_per_tenant", 1)
    await _create(client, ALICE, name="One", publish=True)
    await _create(client, ALICE, name="Two", publish=True)
    await _create(
        client,
        ADMIN,
        name="Org One",
        owner="organization",
        visibility="tenant",
        publish=True,
    )
    resp = await client.post(
        "/api/skills",
        json={
            "name": "Org Two",
            "body_markdown": "x",
            "owner": "organization",
            "visibility": "tenant",
            "publish": True,
        },
        headers=ADMIN,
    )
    assert resp.status_code == 409


async def test_one_person_can_own_only_so_many(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_skills_per_user", 1)
    await _create(client, ALICE)
    resp = await client.post(
        "/api/skills",
        json={"name": "Another", "body_markdown": "x"},
        headers=ALICE,
    )
    assert resp.status_code == 409


# ── Import and export ─────────────────────────────────────────────────


async def test_a_skill_folder_round_trips_through_export_and_import(
    client: AsyncClient,
) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "pdf-tools/SKILL.md",
            "---\nname: pdf-tools\ndescription: PDFs. Use for PDFs.\n"
            "license: MIT\n---\n# PDF\nSteps",
        )
        z.writestr(
            "pdf-tools/faden.yaml",
            "title: PDF Tools\nforbidden_tools: [web.fetch]\n",
        )
        z.writestr("pdf-tools/references/forms.md", "Forms")
        z.writestr("pdf-tools/scripts/fill.py", "print('x')")
    resp = await client.post(
        "/api/skills/import",
        files={"file": ("pdf-tools.zip", buf.getvalue(), "application/zip")},
        headers=ALICE,
    )
    assert resp.status_code == 201, resp.text
    skill = resp.json()
    assert skill["name"] == "PDF Tools"
    assert skill["frontmatter"] == {"license": "MIT"}
    assert skill["manifest"] == {"forbidden_tools": ["web.fetch"]}
    assert {f["path"]: f["executable"] for f in skill["files"]} == {
        "references/forms.md": False,
        "scripts/fill.py": True,
    }

    exported = await client.get(f"/api/skills/{skill['id']}/export", headers=ALICE)
    assert exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        names = set(z.namelist())
        skill_md = z.read("pdf-tools/SKILL.md").decode()
    assert names == {
        "pdf-tools/SKILL.md",
        "pdf-tools/faden.yaml",
        "pdf-tools/references/forms.md",
        "pdf-tools/scripts/fill.py",
    }
    assert skill_md.startswith(
        "---\nname: pdf-tools\ndescription: PDFs. Use for PDFs.\nlicense: MIT\n---",
    )

    md = await client.get(f"/api/skills/{skill['id']}/export?format=md", headers=ALICE)
    assert md.headers["content-type"].startswith("text/markdown")
    assert "# PDF" in md.text


async def test_importing_a_skill_md(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/skills/import",
        files={
            "file": (
                "SKILL.md",
                b"---\nname: tidy\ndescription: d\n---\nBody",
                "text/markdown",
            ),
        },
        data={"publish": "true"},
        headers=ALICE,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["slug"] == "tidy"
    assert resp.json()["published_version"] == 1


async def test_a_broken_file_is_a_422_not_a_500(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/skills/import",
        files={"file": ("bad.zip", b"PK\x03\x04 not really", "application/zip")},
        headers=ALICE,
    )
    assert resp.status_code == 422


# ── The rest ──────────────────────────────────────────────────────────


async def test_the_editor_completions_can_be_reached(client: AsyncClient) -> None:
    """``/completions`` came after ``/{skill_id}`` and was taken for an id."""
    resp = await client.get("/api/skills/completions", headers=ALICE)
    assert resp.status_code == 200
    assert "activation_values" in resp.json()


async def test_an_assignment_is_deleted_only_through_its_own_skill(
    client: AsyncClient,
) -> None:
    one = await _create(
        client,
        ADMIN,
        name="One",
        owner="organization",
        visibility="tenant",
    )
    two = await _create(
        client,
        ADMIN,
        name="Two",
        owner="organization",
        visibility="tenant",
    )
    created = await client.post(
        f"/api/skills/{one['id']}/assignments",
        json={"target_type": "tenant"},
        headers=ADMIN,
    )
    assert created.status_code == 201
    assignment = created.json()["id"]
    wrong = await client.delete(
        f"/api/skills/{two['id']}/assignments/{assignment}",
        headers=ADMIN,
    )
    assert wrong.status_code == 404
    mine = await client.post(
        f"/api/skills/{one['id']}/assignments",
        json={"target_type": "tenant"},
        headers=PUBLISHER,
    )
    assert (
        mine.status_code == 403
    )  # they see the tenant's skill but do not administer it
    right = await client.delete(
        f"/api/skills/{one['id']}/assignments/{assignment}",
        headers=ADMIN,
    )
    assert right.status_code == 204


async def test_a_skill_assigned_to_an_agent_comes_in_on_that_agent_only(
    client: AsyncClient,
) -> None:
    """C-1 (sdk/51 F01): the gateway names the agent (``assistant_id``, its id) and its version."""
    agent, other = "6f1c0d2e-0000-4000-8000-00000000a001", "6f1c0d2e-0000-4000-8000-00000000a002"
    skill = await _create(
        client,
        ADMIN,
        name="EPLAN pages",
        owner="organization",
        visibility="tenant",
        publish=True,
    )
    created = await client.post(
        f"/api/skills/{skill['id']}/assignments",
        json={"target_type": "assistant", "target_id": agent},
        headers=ADMIN,
    )
    assert created.status_code == 201, created.text
    asked = {"user_query": "What is on page 12?"}
    assert await _resolve(client, "alice", assistant_id=agent, assistant_version=3, **asked) == ["eplan-pages"]
    assert await _resolve(client, "alice", assistant_id=other, assistant_version=1, **asked) == []
    assert await _resolve(client, "alice", **asked) == [], "a plain chat names no agent"
    bad = await client.post(
        "/api/internal/skills/resolve",
        json={"tenant_id": "default", "user_id": "alice", "assistant_id": agent, "assistant_version": 0},
    )
    assert bad.status_code == 422, "a version is a whole number from 1"


# ── Assigning skills to agents as sets (sdk/51 F01b) ──────────────────

AGENT_1 = "6f1c0d2e-0000-4000-8000-00000000b001"
AGENT_2 = "6f1c0d2e-0000-4000-8000-00000000b002"
AGENT_3 = "6f1c0d2e-0000-4000-8000-00000000b003"
OTHER_ADMIN = as_user("admin-2", caps=("admin",), tenant="other")


def _agent(n: int) -> str:
    return f"6f1c0d2e-0000-4000-8000-{n:012d}"


async def _org_skill(client: AsyncClient, name: str, **fields: Any) -> dict[str, Any]:
    return await _create(
        client,
        ADMIN,
        name=name,
        owner="organization",
        visibility="tenant",
        **fields,
    )


async def _assign(
    client: AsyncClient,
    skill_id: str,
    headers: dict[str, str] = ADMIN,
    **body: Any,
) -> Any:
    return await client.post(
        f"/api/skills/{skill_id}/assignments",
        json=body,
        headers=headers,
    )


async def _put_agents(
    client: AsyncClient,
    skill_id: str,
    agent_ids: list[str],
    headers: dict[str, str] = ADMIN,
) -> Any:
    return await client.put(
        f"/api/skills/{skill_id}/assignments/agents",
        json={"agent_ids": agent_ids},
        headers=headers,
    )


async def _put_skills(
    client: AsyncClient,
    agent_id: str,
    skill_ids: list[str],
    headers: dict[str, str] = ADMIN,
) -> Any:
    return await client.put(
        f"/api/skills/assignments/agents/{agent_id}",
        json={"skill_ids": skill_ids},
        headers=headers,
    )


async def _rows(client: AsyncClient, skill_id: str) -> list[dict[str, Any]]:
    resp = await client.get(f"/api/skills/{skill_id}/assignments", headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_the_skill_side_put_replaces_only_its_agent_rows(
    client: AsyncClient,
) -> None:
    skill = await _org_skill(client, "Pages")
    everyone = (await _assign(client, skill["id"], target_type="tenant")).json()
    first = (
        await _assign(
            client,
            skill["id"],
            target_type="assistant",
            target_id=AGENT_1,
            priority_override=70,
        )
    ).json()

    resp = await _put_agents(client, skill["id"], [AGENT_1, AGENT_2])
    assert resp.status_code == 200, resp.text
    by_target = {(r["target_type"], r["target_id"]): r for r in resp.json()}
    assert set(by_target) == {
        ("tenant", None),
        ("assistant", AGENT_1),
        ("assistant", AGENT_2),
    }
    assert by_target[("tenant", None)]["id"] == everyone["id"], (
        "another target's row stays"
    )
    kept = by_target[("assistant", AGENT_1)]
    assert (kept["id"], kept["priority_override"]) == (first["id"], 70), (
        "a kept row keeps its id and override"
    )

    again = await _put_agents(client, skill["id"], [AGENT_2, AGENT_1])
    assert sorted(r["id"] for r in again.json()) == sorted(
        r["id"] for r in resp.json()
    ), "the same set again changes nothing"

    fewer = await _put_agents(client, skill["id"], [AGENT_2])
    assert {(r["target_type"], r["target_id"]) for r in fewer.json()} == {
        ("tenant", None),
        ("assistant", AGENT_2),
    }
    cleared = await _put_agents(client, skill["id"], [])
    assert [r["target_type"] for r in cleared.json()] == ["tenant"]


async def test_an_agent_new_to_the_skill_must_be_vetted_under_the_lock(
    client: AsyncClient,
) -> None:
    """The backend names the agents the caller can see; one the skill does not have yet must be among them.

    Decided here, under the tenant's assignment lock: an agent another change removed a moment before is new
    (the backend read the skill's agents before that change and could not tell).
    """
    skill = await _org_skill(client, "Vetted")
    assert (await _put_agents(client, skill["id"], [AGENT_1])).status_code == 200

    async def put(agent_ids: list[str], vetted: list[str]) -> Any:
        return await client.put(
            f"/api/skills/{skill['id']}/assignments/agents",
            json={"agent_ids": agent_ids, "vetted_agent_ids": vetted},
            headers=ADMIN,
        )

    resp = await put([AGENT_1, AGENT_2, AGENT_3], [AGENT_2])
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == {
        "code": "unknown_agent",
        "message": f"No such agent: {AGENT_3}",
        "ids": [AGENT_3],
    }, "AGENT_1 the skill has already may stay unvetted; AGENT_3 is new and not vetted"
    assert [r["target_id"] for r in await _rows(client, skill["id"])] == [AGENT_1], (
        "nothing changed"
    )

    resp = await put([AGENT_1, AGENT_2], [AGENT_2])
    assert resp.status_code == 200, resp.text

    # Removed by another change, then named again with the stale view: new now, so it must be vetted.
    assert (await _put_agents(client, skill["id"], [AGENT_2])).status_code == 200
    resp = await put([AGENT_1, AGENT_2], [AGENT_2])
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["ids"] == [AGENT_1]


async def test_the_skill_side_put_drops_a_second_row_for_one_agent(
    client: AsyncClient,
) -> None:
    """The old dialog took the same agent twice; the set keeps one row."""
    from faden_skills.db.session import engine  # noqa: PLC0415

    skill = await _org_skill(client, "Twice")
    first = (
        await _assign(client, skill["id"], target_type="assistant", target_id=AGENT_1)
    ).json()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO skill_assignments "
                "(id, skill_id, target_type, target_id, enabled) "
                "VALUES (gen_random_uuid(), :skill, 'assistant', :agent, true)",
            ),
            {"skill": skill["id"], "agent": AGENT_1},
        )
    assert len(await _rows(client, skill["id"])) == 2
    resp = await _put_agents(client, skill["id"], [AGENT_1])
    assert [r["id"] for r in resp.json()] == [first["id"]]


async def test_assigning_to_agents_needs_admin_access(client: AsyncClient) -> None:
    org = await _org_skill(client, "Org")
    assert (
        await _put_agents(client, org["id"], [AGENT_1], PUBLISHER)
    ).status_code == 403
    own = await _create(client, ALICE, name="Mine")
    assert (
        await _put_agents(client, own["id"], [AGENT_1], ALICE)
    ).status_code == 403, "owning a skill is not administering its assignments"
    assert (
        await _put_skills(client, AGENT_1, [org["id"]], PUBLISHER)
    ).status_code == 403
    assert await _rows(client, org["id"]) == []


async def test_a_request_names_at_most_100_ids_each_once(client: AsyncClient) -> None:
    skill = await _org_skill(client, "Bounded")
    assert (
        await _put_agents(client, skill["id"], [_agent(n) for n in range(101)])
    ).status_code == 422
    assert (
        await _put_agents(client, skill["id"], [AGENT_1, AGENT_1])
    ).status_code == 422
    assert (await _put_agents(client, skill["id"], [""])).status_code == 422
    assert (await _put_agents(client, skill["id"], ["x" * 129])).status_code == 422
    legacy = await _put_agents(client, skill["id"], ["typed-in-the-old-dialog"])
    assert legacy.status_code == 200, "an old id stays; the backend checks new ones"
    assert (
        await _put_skills(client, AGENT_1, [skill["id"], skill["id"]])
    ).status_code == 422
    many = [str(uuid.UUID(int=n + 1)) for n in range(101)]
    assert (await _put_skills(client, AGENT_1, many)).status_code == 422
    resp = await _put_agents(client, skill["id"], [_agent(n) for n in range(100)])
    assert resp.status_code == 200
    assert len(resp.json()) == 100


async def test_a_tenant_has_up_to_1000_assignments(client: AsyncClient) -> None:
    assert settings.max_assignments_per_tenant == 1000
    skills = [await _org_skill(client, f"Limit {n}") for n in range(11)]
    for n, skill in enumerate(skills[:10]):
        resp = await _put_agents(
            client,
            skill["id"],
            [_agent(n * 100 + k) for k in range(100)],
        )
        assert resp.status_code == 200, resp.text

    over = await _put_agents(client, skills[10]["id"], [AGENT_1])
    assert over.status_code == 409
    assert "1000" in over.json()["detail"]
    assert await _rows(client, skills[10]["id"]) == []
    assert (
        await _assign(client, skills[10]["id"], target_type="tenant")
    ).status_code == 409
    assert (await _put_skills(client, AGENT_1, [skills[10]["id"]])).status_code == 409

    swap = await _put_agents(
        client,
        skills[0]["id"],
        [_agent(k) for k in range(99)] + [AGENT_1],
    )
    assert swap.status_code == 200, "removing as many as it adds stays within the limit"
    fewer = await _put_agents(client, skills[0]["id"], [AGENT_1])
    assert fewer.status_code == 200, "removing is always possible"
    assert (await _put_agents(client, skills[10]["id"], [AGENT_2])).status_code == 200


async def test_the_same_target_twice_is_refused(client: AsyncClient) -> None:
    skill = await _org_skill(client, "Once")
    assert (await _assign(client, skill["id"], target_type="tenant")).status_code == 201
    again = await _assign(client, skill["id"], target_type="tenant")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "already_assigned"
    assert (
        await _assign(client, skill["id"], target_type="assistant", target_id=AGENT_1)
    ).status_code == 201
    twice = await _assign(
        client,
        skill["id"],
        target_type="assistant",
        target_id=AGENT_1,
    )
    assert twice.status_code == 409
    assert twice.json()["detail"]["code"] == "already_assigned"
    assert (
        await _assign(client, skill["id"], target_type="assistant", target_id=AGENT_2)
    ).status_code == 201, "another agent is another target"
    assert len(await _rows(client, skill["id"])) == 3


async def test_an_agents_skills_list_its_own_and_everyones_as_the_caller_sees_them(
    client: AsyncClient,
) -> None:
    own = await _org_skill(client, "Agent One Skill")
    other = await _org_skill(client, "Agent Two Skill")
    shared = await _org_skill(client, "For Everyone")
    off = await _org_skill(client, "Switched Off")
    hidden = await _create(client, BOB, name="Bobs Notes")
    await _put_agents(client, own["id"], [AGENT_1])
    await _put_agents(client, other["id"], [AGENT_2])
    await _put_agents(client, hidden["id"], [AGENT_1])
    await _assign(client, shared["id"], target_type="tenant")
    await _assign(client, shared["id"], target_type="assistant", target_id=AGENT_1)
    await _assign(client, off["id"], target_type="tenant", enabled=False)

    def seen(resp: Any) -> set[tuple[str, str, str]]:
        assert resp.status_code == 200, resp.text
        return {(s["slug"], s["via"], s["access"]) for s in resp.json()}

    as_admin = await client.get(
        f"/api/skills/assignments/agents/{AGENT_1}",
        headers=ADMIN,
    )
    assert seen(as_admin) == {
        ("agent-one-skill", "agent", "admin"),
        ("bobs-notes", "agent", "admin"),
        ("for-everyone", "agent", "admin"),
        ("for-everyone", "everyone", "admin"),
    }
    as_alice = await client.get(
        f"/api/skills/assignments/agents/{AGENT_1}",
        headers=ALICE,
    )
    assert seen(as_alice) == {
        ("agent-one-skill", "agent", "read"),
        ("for-everyone", "agent", "read"),
        ("for-everyone", "everyone", "read"),
    }, "Bob's private skill is not Alice's to see"
    first = as_admin.json()[0]
    assert set(first) == {"id", "name", "slug", "status", "access", "via"}


async def test_the_agent_side_put_changes_only_the_callers_skills(
    client: AsyncClient,
) -> None:
    one = await _org_skill(client, "One", publish=True)
    two = await _org_skill(client, "Two", publish=True)
    elsewhere = await _create(
        client,
        OTHER_ADMIN,
        name="Elsewhere",
        owner="organization",
        visibility="global",
        publish=True,
    )
    assert (
        await _put_agents(client, elsewhere["id"], [AGENT_1], OTHER_ADMIN)
    ).status_code == 200
    await _put_agents(client, one["id"], [AGENT_1])

    resp = await _put_skills(client, AGENT_1, [two["id"]])
    assert resp.status_code == 200, resp.text
    assert {(s["slug"], s["via"], s["access"]) for s in resp.json()} == {
        ("two", "agent", "admin"),
        ("elsewhere", "agent", "read"),
    }, "another organisation's skill stays; this admin may only read it"
    assert set(
        await _resolve(client, "alice", assistant_id=AGENT_1, user_query="hi"),
    ) == {
        "two",
        "elsewhere",
    }, "what the tab shows is what the agent's chats get"

    refused = await _put_skills(client, AGENT_1, [one["id"], elsewhere["id"]])
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "not_your_skill"
    unknown = await _put_skills(client, AGENT_1, [one["id"], str(uuid.uuid4())])
    assert unknown.status_code == 404
    after = await client.get(f"/api/skills/assignments/agents/{AGENT_1}", headers=ADMIN)
    assert {s["slug"] for s in after.json()} == {"two", "elsewhere"}, (
        "a refused request changes nothing"
    )

    cleared = await _put_skills(client, AGENT_1, [])
    assert [s["slug"] for s in cleared.json()] == ["elsewhere"]
    assert await _rows(client, two["id"]) == []


async def test_a_change_to_assignments_waits_for_the_tenants_lock(
    client: AsyncClient,
) -> None:
    """Two changes at once cannot both pass the duplicate check or the limit."""
    from faden_skills.db.session import engine  # noqa: PLC0415
    from faden_skills.services.dao import _lock_key  # noqa: PLC0415

    skill = await _org_skill(client, "Locked")
    async with engine.connect() as conn:
        held = await conn.begin()
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _lock_key("skill-assignments:default")},
        )
        put = asyncio.ensure_future(_put_agents(client, skill["id"], [AGENT_1]))
        await asyncio.sleep(0.5)
        assert not put.done(), "it waits while another change holds the lock"
        await held.rollback()
    resp = await asyncio.wait_for(put, 10)
    assert resp.status_code == 200, resp.text


async def test_a_deleted_agent_loses_its_assignments(client: AsyncClient) -> None:
    one = await _org_skill(client, "Kept Elsewhere")
    await _put_agents(client, one["id"], [AGENT_1, AGENT_3])
    await _assign(client, one["id"], target_type="tenant")
    resp = await client.delete(f"/api/internal/agents/{AGENT_1}/assignments")
    assert resp.status_code == 200
    assert resp.json() == {"removed": 1}
    assert {
        (r["target_type"], r["target_id"]) for r in await _rows(client, one["id"])
    } == {
        ("assistant", AGENT_3),
        ("tenant", None),
    }
    unauthenticated = await client.delete(
        f"/api/internal/agents/{AGENT_3}/assignments",
        headers={"Authorization": "Bearer wrong"},
    )
    assert unauthenticated.status_code in {401, 403}


async def test_uses_are_counted(client: AsyncClient) -> None:
    skill = await _create(client, ALICE, publish=True)
    for _ in range(3):
        resp = await client.post(
            "/api/internal/skills/usage",
            json={"tenant_id": "default", "skill_id": skill["id"], "version": 1},
        )
        assert resp.status_code == 204
    # An unknown skill is ignored, not an error.
    resp = await client.post(
        "/api/internal/skills/usage",
        json={
            "tenant_id": "default",
            "skill_id": "00000000-0000-0000-0000-000000000000",
            "version": 1,
        },
    )
    assert resp.status_code == 204
    seen = (await client.get(f"/api/skills/{skill['id']}", headers=ALICE)).json()
    assert seen["uses_30d"] == 3


async def test_the_picker_lists_only_usable_skills(client: AsyncClient) -> None:
    await _create(client, ALICE, name="Draft Only")
    live = await _create(client, ALICE, name="Live", publish=True)
    archived = await _create(client, ALICE, name="Gone", publish=True)
    await client.post(f"/api/skills/{archived['id']}/archive", headers=ALICE)
    usable = (await client.get("/api/skills?usable=true", headers=ALICE)).json()
    assert [s["id"] for s in usable["items"]] == [live["id"]]


async def test_archiving_and_restoring(client: AsyncClient) -> None:
    skill = await _create(client, ALICE, activation="manual", publish=True)
    await client.post(f"/api/skills/{skill['id']}/archive", headers=ALICE)
    assert await _resolve(client, "alice", requested=["code-review"]) == []
    restored = (
        await client.post(f"/api/skills/{skill['id']}/restore", headers=ALICE)
    ).json()
    assert restored["status"] == "published"
    assert await _resolve(client, "alice", requested=["code-review"]) == ["code-review"]


async def test_deleting_takes_the_versions_with_it(client: AsyncClient) -> None:
    skill = await _create(client, ALICE)
    assert (
        await client.delete(f"/api/skills/{skill['id']}", headers=BOB)
    ).status_code == 404
    assert (
        await client.delete(f"/api/skills/{skill['id']}", headers=ALICE)
    ).status_code == 204
    assert (
        await client.get(f"/api/skills/{skill['id']}", headers=ALICE)
    ).status_code == 404


# ── Files and file types, for chats ───────────────────────────────────


async def _with_files(client: AsyncClient) -> dict[str, Any]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "sheets/SKILL.md",
            "---\nname: sheets\ndescription: Spreadsheets. Use for xlsx.\n---\n"
            "Read references/formulas.md when a formula is involved.",
        )
        z.writestr(
            "sheets/faden.yaml",
            "activation: auto\ntrigger_rules:\n  file_types: [xlsx]\n",
        )
        z.writestr("sheets/references/formulas.md", "SUMIF sums what matches.")
        z.writestr("sheets/references/long.md", "x" * 5000)
        z.writestr("sheets/scripts/check.py", "print('ok')")
        z.writestr("sheets/assets/logo.png", b"\x89PNG\x00\x01")
    resp = await client.post(
        "/api/skills/import",
        files={"file": ("sheets.zip", buf.getvalue(), "application/zip")},
        data={"publish": "true"},
        headers=ALICE,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_a_file_type_trigger_fires_when_the_chat_has_such_a_file(
    client: AsyncClient,
) -> None:
    await _with_files(client)
    assert await _resolve(client, "alice", user_query="sum column B") == []
    assert await _resolve(
        client,
        "alice",
        user_query="sum column B",
        file_types=["xlsx"],
    ) == ["sheets"]


async def test_a_resolved_skill_lists_its_files_and_inlines_what_fits(
    client: AsyncClient,
) -> None:
    await _with_files(client)
    resp = await client.post(
        "/api/internal/skills/resolve",
        json={
            "tenant_id": "default",
            "user_id": "alice",
            "requested": ["sheets"],
            "inline_files_chars": 1000,
        },
    )
    [skill] = resp.json()["skills"]
    files = {f["path"]: f for f in skill["files"]}
    assert set(files) == {
        "references/formulas.md",
        "references/long.md",
        "scripts/check.py",
        "assets/logo.png",
    }
    assert files["references/formulas.md"]["text"] == "SUMIF sums what matches."
    assert files["references/long.md"]["text"] is None  # over the budget
    assert files["scripts/check.py"]["text"] is None  # scripts are listed only
    assert files["assets/logo.png"]["kind"] == "binary"

    resp = await client.post(
        "/api/internal/skills/resolve",
        json={"tenant_id": "default", "user_id": "alice", "requested": ["sheets"]},
    )
    assert all(f["text"] is None for f in resp.json()["skills"][0]["files"])


async def test_a_chat_reads_a_file_of_the_published_version_only(
    client: AsyncClient,
) -> None:
    skill = await _with_files(client)
    url = f"/api/internal/skills/{skill['id']}/versions/1/file"
    resp = await client.get(
        url,
        params={"tenant_id": "default", "path": "references/long.md"},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "x" * 5000
    resp = await client.get(
        url,
        params={"tenant_id": "default", "path": "assets/logo.png"},
    )
    assert resp.json() == {
        "path": "assets/logo.png",
        "size": 6,
        "kind": "binary",
        "text": None,
    }
    missing = await client.get(url, params={"tenant_id": "default", "path": "nope.md"})
    assert missing.status_code == 404
    other_tenant = await client.get(
        url,
        params={"tenant_id": "t2", "path": "references/long.md"},
    )
    assert other_tenant.status_code == 404

    await client.post(
        f"/api/skills/{skill['id']}/versions",
        json={"body_markdown": "v2"},
        headers=ALICE,
    )
    draft = await client.get(
        f"/api/internal/skills/{skill['id']}/versions/2/file",
        params={"tenant_id": "default", "path": "references/long.md"},
    )
    assert draft.status_code == 404  # not published, so no chat has it
