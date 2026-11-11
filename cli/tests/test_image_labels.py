"""Each published image says what it is: its own title and description, on the image index and in its Dockerfile.

GHCR shows a package's description from the image's OCI annotations; our images are indexes (the image and its SBOM
attestation), and for an index it reads the index's annotations. ``docker-publish.yml`` gives every image its title
and description, and ``docker-build-reusable.yml`` writes them as labels and as annotations on the manifest and the
index. The same keys are LABELs in each Dockerfile's published stage, for images built anywhere else (a test server
building a release itself); this keeps the two equal, so neither drifts.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLISH = WORKFLOWS / "docker-publish.yml"
REUSABLE = WORKFLOWS / "docker-build-reusable.yml"
SOURCE = "https://github.com/fadenstack/fadenstack"

if not PUBLISH.exists():  # the CLI's own sdist has no workflows
    pytest.skip("not a checkout of the whole repository", allow_module_level=True)


def _images() -> list[dict[str, str]]:
    """Every image docker-publish.yml builds, with the reusable workflow's inputs (matrix rows expanded)."""
    jobs = yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))["jobs"]
    images = []
    for name, job in jobs.items():
        if job.get("uses") != "./.github/workflows/docker-build-reusable.yml":
            continue
        rows = (job.get("strategy") or {}).get("matrix", {}).get("include") or [{}]
        for row in rows:
            inputs = {}
            for key, value in job["with"].items():
                value = str(value)
                for field, field_value in row.items():
                    value = value.replace("${{ matrix.%s }}" % field, str(field_value))
                inputs[key] = value
            inputs["job"] = name + (f"[{row['variant']}]" if "variant" in row else "")
            images.append(inputs)
    return images


def _stage_labels(dockerfile: Path, target: str | None) -> dict[str, str]:
    """The LABELs set in one stage of a Dockerfile (the last stage when no target is named)."""
    text = re.sub(r"\\\r?\n", " ", dockerfile.read_text(encoding="utf-8"))
    stages: list[tuple[str | None, dict[str, str]]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line.partition(" ")
        if word.upper() == "FROM":
            match = re.search(r"\s+AS\s+(\S+)\s*$", rest, re.IGNORECASE)
            stages.append((match.group(1) if match else None, {}))
        elif word.upper() == "LABEL":
            for pair in shlex.split(rest):
                key, _, value = pair.partition("=")
                stages[-1][1][key] = value
    if target is None:
        return stages[-1][1]
    for name, labels in stages:
        if name == target:
            return labels
    raise AssertionError(f"{dockerfile} has no stage {target}")


IMAGES = _images()


def test_every_image_is_built_with_its_own_title_and_description() -> None:
    services = {image["job"] for image in IMAGES}
    assert services == {
        "base", "gateway", "backend", "frontend", "pii", "pii-neural[cpu]", "pii-neural[gpu]", "mcp", "skills"
    }
    descriptions = [image["description"] for image in IMAGES]
    titles = [image["title"] for image in IMAGES]
    assert len(set(descriptions)) == len(IMAGES), "an image reuses another's description"
    assert len(set(titles)) == len(IMAGES), "an image reuses another's title"


@pytest.mark.parametrize("image", IMAGES, ids=[image["job"] for image in IMAGES])
def test_the_description_is_what_ghcr_takes(image: dict[str, str]) -> None:
    # GitHub: plain text, at most 512 characters. A newline would split metadata-action's key=value lists, and
    # metadata-action reads "#" as the start of a comment.
    for text in (image["title"], image["description"]):
        assert text and text == text.strip()
        assert "\n" not in text and "#" not in text and "${{" not in text
    assert len(image["description"]) <= 512


@pytest.mark.parametrize("image", IMAGES, ids=[image["job"] for image in IMAGES])
def test_the_dockerfile_labels_its_published_stage_the_same(image: dict[str, str]) -> None:
    labels = _stage_labels(ROOT / image["context"] / "Dockerfile", image.get("target"))
    assert labels.get("org.opencontainers.image.title") == image["title"]
    assert labels.get("org.opencontainers.image.description") == image["description"]
    assert labels.get("org.opencontainers.image.source") == SOURCE
    assert labels.get("org.opencontainers.image.licenses") == image.get("licenses", "Apache-2.0")


def test_the_reusable_workflow_annotates_the_index() -> None:
    workflow = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))
    inputs = workflow[True]["workflow_call"]["inputs"]  # PyYAML reads the key "on" as True
    assert inputs["title"]["required"] and inputs["description"]["required"]
    assert inputs["licenses"]["default"] == "Apache-2.0"
    steps = workflow["jobs"]["build-and-push"]["steps"]
    meta = next(step for step in steps if step.get("id") == "meta")
    assert set(meta["env"]["DOCKER_METADATA_ANNOTATIONS_LEVELS"].split(",")) == {"manifest", "index"}
    for field in ("labels", "annotations"):
        lines = meta["with"][field].splitlines()
        assert "org.opencontainers.image.title=${{ inputs.title }}" in lines
        assert "org.opencontainers.image.description=${{ inputs.description }}" in lines
        assert "org.opencontainers.image.licenses=${{ inputs.licenses }}" in lines
    push = next(step for step in steps if step.get("name") == "Build and push")
    assert push["with"]["annotations"] == "${{ steps.meta.outputs.annotations }}"
    assert push["with"]["sbom"] is True  # why the push is an index at all
