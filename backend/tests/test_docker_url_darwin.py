"""Which Docker Engine the backend talks to by default: unchanged on Linux, Docker Desktop's on a Mac."""

from __future__ import annotations

import pytest

from faden_backend.services.docker.client import _DEFAULT_DOCKER_URL, DockerService, default_docker_url


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_linux_and_windows_are_unchanged_whatever_the_environment(platform: str) -> None:
    url = default_docker_url(platform, {"DOCKER_HOST": "tcp://elsewhere:2375"}, "/home/x", lambda p: False)
    assert url == _DEFAULT_DOCKER_URL


def test_a_mac_honours_docker_host_first() -> None:
    assert default_docker_url("darwin", {"DOCKER_HOST": "unix:///tmp/d.sock"}, "/Users/x", lambda p: True) == "unix:///tmp/d.sock"


def test_a_mac_uses_the_default_socket_when_docker_desktop_provides_it() -> None:
    assert default_docker_url("darwin", {}, "/Users/x", lambda p: True) == "unix:///var/run/docker.sock"


def test_a_mac_without_the_default_socket_uses_docker_desktops_own() -> None:
    present = {"/Users/x/.docker/run/docker.sock"}
    assert default_docker_url("darwin", {}, "/Users/x", present.__contains__) == "unix:///Users/x/.docker/run/docker.sock"


def test_an_explicit_url_still_wins() -> None:
    assert DockerService("unix:///tmp/explicit.sock")._url == "unix:///tmp/explicit.sock"  # noqa: SLF001
