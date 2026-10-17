"""Pydantic schemas for the admin containers API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from faden_backend.db.models.containers import ContainerClass, ContainerPolicy


class ContainerSummaryDTO(BaseModel):
    """Lightweight summary of a running/stopped container."""

    id: str
    name: str
    image: str
    status: str
    state: str
    created: str
    ports: list[dict[str, Any]] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    endpoint: str = Field(default="", description="Published host:port bindings, e.g. '0.0.0.0:8080'.")
    container_class: ContainerClass = ContainerClass.UNTRUSTED
    policy: ContainerPolicy = ContainerPolicy.FREE
    owner_scope: str = "unknown"

    model_config = ConfigDict(from_attributes=True)

    @field_validator("ports", "networks", mode="before")
    @classmethod
    def _coerce_nullable_lists(cls, value: Any) -> Any:
        return [] if value is None else value


class ContainerDetailDTO(ContainerSummaryDTO):
    """A container's detail page.

    Not Docker's inspect output: that holds the environment and command line, with the platform's keys and
    passwords in them. What the edit form needs comes from ``/settings``, with secret values hidden.
    """


class LifecycleAction(str):
    """Valid lifecycle action names."""

    START = "start"
    STOP = "stop"
    RESTART = "restart"
    PAUSE = "pause"
    UNPAUSE = "unpause"


class RegisterContainerRequest(BaseModel):
    """Body for manually registering/classifying a container."""

    container_class: ContainerClass = ContainerClass.TENANT_APP
    owner_scope: str = "platform"
    policy: ContainerPolicy = ContainerPolicy.FREE


class PortBinding(BaseModel):
    """A single host→container port mapping."""

    host_port: str = Field(..., description="Host port, e.g. '8080'.")
    container_port: str = Field(..., description="Container port/protocol, e.g. '80/tcp'.")


class ContainerSettingsDTO(BaseModel):
    """A container as its edit form shows it: what it was created with, not what its image supplies."""

    image: str
    name: str
    cmd: list[str] | None = Field(None, description="Secret flag values hidden (secret_redaction.MASK).")
    env: list[str] = Field(
        default_factory=list,
        description="KEY=VALUE strings; secret values hidden. Sent back unchanged, a hidden value is kept.",
    )
    #: Names in ``env`` whose value (or a URL's password) is hidden.
    hidden_env: list[str] = Field(default_factory=list)
    #: Whether a value in ``cmd`` is hidden.
    cmd_hidden: bool = False
    ports: list[PortBinding] = Field(default_factory=list)
    volumes: list[str] = Field(default_factory=list)
    network: str = ""
    running: bool = False
    container_class: ContainerClass
    owner_scope: str
    policy: ContainerPolicy
    #: Made by Compose (Fadenstack's own services): the next deploy or upgrade
    #: makes it again from the compose file and .env.
    managed_by_compose: bool = False
    #: The backend runs in it, and cannot replace the container it runs in.
    backend_runs_here: bool = False


class CreateContainerRequest(BaseModel):
    """Request body for creating a new container."""

    image: str = Field(..., description="Image name:tag, e.g. 'nginx:latest'.")
    name: str | None = Field(None, description="Optional container name.")
    container_class: ContainerClass = ContainerClass.UNTRUSTED
    owner_scope: str = "platform"
    policy: ContainerPolicy = ContainerPolicy.FREE
    auto_start: bool = False
    ports: list[PortBinding] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list, description="KEY=VALUE strings.")
    cmd: list[str] | None = Field(None, description="Command override.")
    network: str | None = None
    volumes: list[str] = Field(default_factory=list, description="'/host:/container' strings.")
