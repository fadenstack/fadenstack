"""SKILL.md, llmport.yaml and zipped skill folders."""

from __future__ import annotations

import io
import zipfile

import pytest

from llm_port_skills.services import bundle as fmt


def test_a_spec_skill_reads_as_written() -> None:
    parsed = fmt.from_markdown(
        "---\n"
        "name: pdf-processing\n"
        "description: Extract PDF text. Use when handling PDFs.\n"
        "license: Apache-2.0\n"
        "allowed-tools: Bash(git:*) Read\n"
        "metadata:\n  author: example-org\n"
        "---\n"
        "# PDF\n\nDo things.\n",
    )
    assert parsed.name == "pdf-processing"
    assert parsed.description == "Extract PDF text. Use when handling PDFs."
    assert parsed.body == "# PDF\n\nDo things."
    assert parsed.frontmatter == {
        "license": "Apache-2.0",
        "allowed-tools": "Bash(git:*) Read",
        "metadata": {"author": "example-org"},
    }
    # The spec's allowed-tools is another agent's permission list; read as
    # llm.port's it would strip every MCP tool from the chat.
    assert parsed.manifest == {}


def test_the_frontmatter_ends_at_a_line_of_three_dashes_only() -> None:
    """The old parser ended it at the first ``---`` anywhere."""
    parsed = fmt.from_markdown(
        "---\nname: dashes\ndescription: a --- b\n---\nBody\n\n----\n\nMore\n",
    )
    assert parsed.description == "a --- b"
    assert parsed.body == "Body\n\n----\n\nMore"


def test_invalid_frontmatter_is_an_error_not_an_empty_one() -> None:
    with pytest.raises(fmt.BundleError, match="not valid YAML"):
        fmt.from_markdown("---\nname: [unclosed\n---\nBody")


def test_an_old_llm_port_document_moves_its_fields_where_they_belong() -> None:
    parsed = fmt.from_markdown(
        "---\n"
        "name: Financial Analysis\n"
        "description: Trends\n"
        "scope: tenant\n"
        "status: published\n"
        "priority: 70\n"
        "tags: [finance]\n"
        "allowed_tools: [mcp.search]\n"
        "trigger_rules:\n  keywords: [revenue]\n"
        "---\n"
        "Steps",
    )
    assert parsed.name == "financial-analysis"
    assert parsed.catalog == {
        "title": "Financial Analysis",
        "tags": ["finance"],
        "priority": 70,
    }
    assert parsed.manifest == {
        "allowed_tools": ["mcp.search"],
        "trigger_rules": {"keywords": ["revenue"]},
    }
    # ``status: published`` in a document used to publish it on save,
    # past the published-skills limit.
    assert "status" not in parsed.frontmatter
    assert "scope" not in parsed.frontmatter


def test_a_document_without_a_name_needs_one_from_elsewhere() -> None:
    with pytest.raises(fmt.BundleError, match="no name"):
        fmt.from_markdown("Just instructions")
    assert (
        fmt.from_markdown("Just instructions", default_name="My Notes").name
        == "my-notes"
    )


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("pdf-processing", True),
        ("a1", True),
        ("PDF-Processing", False),
        ("-pdf", False),
        ("pdf-", False),
        ("pdf--processing", False),
        ("x" * 65, False),
    ],
)
def test_names_follow_the_spec(name: str, valid: bool) -> None:
    assert fmt.is_valid_name(name) is valid


def test_slugify_makes_a_valid_name() -> None:
    assert fmt.slugify("Code Review!!  (v2)") == "code-review-v2"
    assert fmt.slugify("???") == "skill"
    assert fmt.is_valid_name(fmt.slugify("a" * 80 + " b"))


def _bundle() -> fmt.SkillBundle:
    return fmt.SkillBundle(
        name="report-writer",
        description="Writes reports. Use for quarterly reports.",
        body="## Goal\nWrite it.",
        frontmatter={"license": "MIT"},
        manifest={"forbidden_tools": ["shell.exec"]},
        catalog={
            "title": "Report Writer",
            "tags": ["docs"],
            "priority": 60,
            "activation": "manual",
        },
        files={
            "references/style.md": {"text": "Be brief."},
            "assets/logo.png": fmt.file_entry(b"\x89PNG\r\n\x1a\n\x00\x00binary"),
        },
    )


def test_a_folder_survives_a_zip_round_trip() -> None:
    original = _bundle()
    again = fmt.from_zip(fmt.to_zip(original))
    assert again.name == original.name
    assert again.description == original.description
    assert again.body == original.body
    assert again.frontmatter == original.frontmatter
    assert again.manifest == original.manifest
    assert again.catalog == original.catalog
    assert again.files == original.files
    assert "base64" in again.files["assets/logo.png"]


def test_a_zip_may_hold_the_folder_or_its_contents() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", "---\nname: flat\ndescription: d\n---\nBody")
        z.writestr("references/a.md", "A")
        z.writestr("__MACOSX/._SKILL.md", "junk")
    parsed = fmt.from_zip(buf.getvalue())
    assert parsed.name == "flat"
    assert list(parsed.files) == ["references/a.md"]


def test_a_zip_cannot_write_outside_the_folder() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", "---\nname: evil\ndescription: d\n---\nBody")
        z.writestr("../../etc/passwd", "root")
    with pytest.raises(fmt.BundleError, match="not a path inside"):
        fmt.from_zip(buf.getvalue())


def test_a_zip_too_large_unpacked_is_refused_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fmt.settings, "max_bundle_bytes", 1000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("SKILL.md", "---\nname: big\ndescription: d\n---\nBody")
        z.writestr("assets/zeros.bin", b"\x00" * 100_000)
    with pytest.raises(fmt.BundleError, match="at most 1000 bytes"):
        fmt.from_zip(buf.getvalue())


def test_a_zip_without_skill_md_is_not_a_skill() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("README.md", "hi")
    with pytest.raises(fmt.BundleError, match=r"no SKILL\.md"):
        fmt.from_zip(buf.getvalue())


def test_scripts_are_marked_as_such() -> None:
    listed = {
        f["path"]: f
        for f in fmt.describe_files(
            _bundle().files | {"scripts/run.py": {"text": "print()"}},
        )
    }
    assert listed["scripts/run.py"]["executable"] is True
    assert listed["references/style.md"]["executable"] is False
    assert listed["assets/logo.png"]["kind"] == "binary"


def test_the_content_hash_names_the_content_not_its_key_order() -> None:
    one = fmt.content_sha256(
        name="n",
        description="d",
        body="b",
        frontmatter={"a": 1, "b": 2},
        manifest={},
        files={},
    )
    two = fmt.content_sha256(
        name="n",
        description="d",
        body="b",
        frontmatter={"b": 2, "a": 1},
        manifest={},
        files={},
    )
    three = fmt.content_sha256(
        name="n",
        description="d",
        body="b!",
        frontmatter={"a": 1, "b": 2},
        manifest={},
        files={},
    )
    assert one == two
    assert one != three


def test_manifest_types_are_checked() -> None:
    assert fmt.normalize_manifest({"allowed_tools": "a, b c"}) == {
        "allowed_tools": ["a", "b", "c"],
    }
    with pytest.raises(fmt.BundleError, match="list of strings"):
        fmt.normalize_manifest({"forbidden_tools": [1, 2]})
    with pytest.raises(fmt.BundleError, match="trigger_rules"):
        fmt.normalize_manifest({"trigger_rules": ["x"]})
