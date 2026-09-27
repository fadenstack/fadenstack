"""A container's summary reads both shapes Docker describes containers in.

Creating a container from the console answered 500 after the container had
been created and started: the summary was built from Docker's inspect, which
nests ``State`` in an object, and the summary wants the list's string. The
console said the create had failed; creating it again met a name conflict
(release VM, 2026-09-25).
"""

from __future__ import annotations

from llm_port_backend.db.models.containers import ContainerClass, ContainerPolicy
from llm_port_backend.web.api.admin.containers.views import _container_summary_from_docker

# What Docker's inspect returns for a container just created and started.
INSPECT = {
    "Id": "5f1c0ffee",
    "Name": "/mcp-everything",
    "Image": "sha256:0a7108bf6c7b",
    "Created": "2026-09-25T21:19:55.8Z",
    "State": {"Status": "running", "Running": True, "ExitCode": 0},
    "Config": {"Image": "node:22-alpine"},
    "NetworkSettings": {
        "Ports": {"3001/tcp": None},
        "Networks": {"llm_port_shared_default": {"IPAddress": "172.18.0.30"}},
    },
}

# What Docker's list returns for the same container.
LISTED = {
    "Id": "5f1c0ffee",
    "Names": ["/mcp-everything"],
    "Image": "node:22-alpine",
    "Created": 1790371195,
    "State": "running",
    "Status": "Up 2 minutes",
    "Ports": [{"PrivatePort": 3001, "Type": "tcp"}],
    "NetworkSettings": {"Networks": {"llm_port_shared_default": {}}},
}


def test_a_container_just_created_is_summarised() -> None:
    """The inspect shape: state from State.Status, the image by name."""
    summary = _container_summary_from_docker(
        INSPECT, ContainerClass.TENANT_APP, ContainerPolicy.FREE, "platform",
    )
    assert summary.name == "mcp-everything"
    assert summary.state == "running"
    assert summary.status == "running"
    assert summary.image == "node:22-alpine"
    assert summary.networks == ["llm_port_shared_default"]
    assert summary.container_class == ContainerClass.TENANT_APP


def test_a_listed_container_is_summarised_as_before() -> None:
    """The list shape is unchanged."""
    summary = _container_summary_from_docker(LISTED)
    assert summary.name == "mcp-everything"
    assert summary.state == "running"
    assert summary.status == "Up 2 minutes"
    assert summary.image == "node:22-alpine"
    assert summary.endpoint == ":3001"
