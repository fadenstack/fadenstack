"""Configuration file (faden.yaml) read/write utilities.

The CLI persists its state in a YAML file located at:
  - ``$FADEN_CONFIG`` env var (if set)
  - ``<install_dir>/faden.yaml``
  - ``%LOCALAPPDATA%/faden/faden.yaml`` (Windows)
  - ``~/.config/faden/faden.yaml`` (Linux/macOS)

The schema is intentionally flat:  top-level scalars for the common
case, with a ``dev:`` section for dev-mode specifics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from faden_cli.core.registry import (
    REPO_DIR_MAP,
    REPO_NAMES,
    repo_clone_url,
)

# Re-export for backward compatibility
__all__ = ["REPO_DIR_MAP", "repo_clone_url"]


# ── Default paths ─────────────────────────────────────────────────


def _default_config_dir() -> Path:
    """Return platform-appropriate config directory."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "faden"
    return Path.home() / ".config" / "faden"


_DEFAULT_CONFIG_DIR = _default_config_dir()
_DEFAULT_CONFIG_FILE = _DEFAULT_CONFIG_DIR / "faden.yaml"


@dataclass
class DevConfig:
    """Dev-mode specific configuration."""

    workspace_dir: str = ""
    clone_method: str = "https"  # "https" or "ssh"
    branch: str = "main"
    repos: list[str] = field(default_factory=lambda: list(REPO_NAMES))
    github_token: str = ""  # GitHub PAT or OAuth token for private repos


@dataclass
class FadenConfig:
    """Top-level CLI configuration."""

    version: int = 1
    install_dir: str = ""
    compose_file: str = "docker-compose.yaml"
    compose_dev_file: str = "docker-compose.dev.yaml"
    profiles: list[str] = field(default_factory=list)
    admin_email: str = ""
    api_url: str = "http://localhost:8000"
    api_token: str = ""
    #: Which runtime images deploy/upgrade put on this server, by the
    #: architecture of the machines that will run models: "all", "none", or
    #: a list such as "x86_64" or "x86_64,aarch64".
    runtime_images: str = "all"
    #: Whether commands say when a newer release is out (core/update_check.py); deploy asks.
    update_check: bool = True
    #: stable, or preview to hear about release candidates too.
    update_channel: str = "stable"
    #: Where the release feed is read; empty: fadenstack.com. A mirror for sites without internet.
    update_feed_url: str = ""
    #: Modules on by default that an administrator switched off (``faden module disable``), with who and
    #: when: ``{name: {"by": OS user, "at": ISO time}}``. Deploy and upgrade leave them off;
    #: ``faden module enable`` removes the entry.
    modules_switched_off: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Preview releases accepted before deploy or upgrade went on (``release_notice``):
    #: ``{release: {"by": OS user, "at": ISO time, "how": "prompt" or "-y"}}``.
    preview_accepted: dict[str, dict[str, str]] = field(default_factory=dict)
    dev: DevConfig = field(default_factory=DevConfig)

    # ── Derived paths ─────────────────────────────────────────

    @property
    def install_path(self) -> Path:
        """Resolved install directory."""
        return Path(self.install_dir).expanduser().resolve() if self.install_dir else Path.cwd()

    @property
    def compose_path(self) -> Path:
        """Path to the main docker-compose file."""
        return self.install_path / self.compose_file

    @property
    def compose_dev_path(self) -> Path:
        """Path to the dev overlay docker-compose file."""
        return self.install_path / self.compose_dev_file

    @property
    def env_path(self) -> Path:
        """Path to the .env file."""
        return self.install_path / ".env"

    @property
    def dev_workspace_path(self) -> Path:
        """Resolved dev workspace directory."""
        if self.dev.workspace_dir:
            return Path(self.dev.workspace_dir).expanduser().resolve()
        return Path.cwd()


# ── Read/write ────────────────────────────────────────────────────


def config_path() -> Path:
    """Return the active config file path."""
    env = os.environ.get("FADEN_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_CONFIG_FILE


def load_config() -> FadenConfig:
    """Load the config from disk, or return defaults if missing."""
    path = config_path()
    if not path.exists():
        return FadenConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _from_dict(data)


def save_config(cfg: FadenConfig) -> Path:
    """Write the config to disk.  Creates parent dirs as needed."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(_to_dict(cfg), default_flow_style=False, sort_keys=False), encoding="utf-8")
    return path


# ── Serialisation helpers ─────────────────────────────────────────


def _from_dict(data: dict[str, Any]) -> FadenConfig:
    """Build a ``FadenConfig`` from a raw dict."""
    dev_data = data.pop("dev", {}) or {}
    dev = DevConfig(**{k: v for k, v in dev_data.items() if k in DevConfig.__dataclass_fields__})
    cfg = FadenConfig(
        **{k: v for k, v in data.items() if k in FadenConfig.__dataclass_fields__ and k != "dev"},
        dev=dev,
    )
    return cfg


def _to_dict(cfg: FadenConfig) -> dict[str, Any]:
    """Serialise a ``FadenConfig`` to a plain dict for YAML."""
    from dataclasses import asdict

    return asdict(cfg)
