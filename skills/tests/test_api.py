"""The skills API against a real database: who sees what, versions, sharing."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from httpx import AsyncClient

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
