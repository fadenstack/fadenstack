"""The provenance engine's public routes, through the gateway.

An agent's SDK marks what it places: it reads the marking policy, has each
placement's record signed, and reports how the placement went. Anyone checking
a signature offline reads the anchors. The engine's own routes take the
gateway's service token; these take the caller's.

- ``GET /v1/agents/{agent}/provenance/policy``: the engine's default
  ``ProvenancePolicy`` (an agent's version holds no provenance settings of its own).
- ``POST /v1/agents/{agent}/provenance/records``: a ``RecordRequest`` without
  ``agent`` -- the route sets it -- signed by the engine: the ``SignedRecord``.
- ``POST /v1/agents/{agent}/provenance/placements``: a ``PlacementReport``,
  kept by the engine for the agent: 202.
- ``GET /v1/provenance/anchors``: the ``Anchors``, without sign-in.
- ``POST /v1/provenance/verify``: a ``VerifyRequest`` checked by the engine's
  detector, for a signed-in user: the ``VerifyResult``. Nothing submitted is
  kept. ``POST /v1/agents/{agent}/provenance/verify`` does the same for an
  agent's token. (The public check page, without sign-in, takes pictures and
  files only; texts are checked by people the installation knows, CoP 2.1.2.)

The agent routes authenticate as the other ``/v1/agents/{agent}/...`` routes
do: an agent token for this agent, or the token of one of its maintainers.
With the module off, or the engine failing, each answers 503
``provenance_unavailable``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette import status

from faden_gateway.services.gateway.agents import AgentRequest
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.errors import GatewayError, error_response, validation_message
from faden_gateway.services.gateway.provenance_client import ProvenanceClient, ProvenanceRequestError
from faden_gateway.web.api.v1.agents import agent_request
from faden_gateway.web.api.v1.views import _get_json_payload

router = APIRouter()
logger = logging.getLogger(__name__)

#: What a check may send: a file of up to 20 MiB (the engine's limit), in base64, and the JSON around it.
VERIFY_MAX_BODY_BYTES = 20 * 1024 * 1024 * 4 // 3 + 64 * 1024

#: How the content placed came from a model's output (contract ``provenance/v1``).
EditKind = Literal[
    "generated", "rewritten", "summarised", "translated", "corrected", "formatted", "extracted",
]
#: Why a layer of a mark was not applied (contract ``provenance/v1``).
NotAppliedReason = Literal[
    "sink-cannot-carry",
    "stripped-by-host",
    "short-text",
    "exempt-edit",
    "no-watermark-remote-model",
    "no-watermark-greedy",
    "runtime-without-watermark",
    "server-unavailable",
]


class RecordBody(BaseModel):
    """A ``RecordRequest`` without ``agent``: a placement to record and sign, the content's hash only."""

    model_config = ConfigDict(extra="forbid")

    #: SHA-256 of the content placed, hex; text normalised first, as ``content_hash`` does.
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(max_length=255)
    edit_kind: EditKind
    receipt_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)


class LayerOutcome(BaseModel):
    """One layer of one mark: applied, or not and why."""

    model_config = ConfigDict(extra="forbid")

    layer: Literal["watermark", "metadata", "fingerprint", "label"]
    method: str = Field(max_length=64)
    applied: bool
    reason: NotAppliedReason | None = None
    #: Read back from the host after placing (true), or not readable there (false); None: not tried.
    verified: bool | None = None


class PlacementBody(BaseModel):
    """A ``PlacementReport``: where the SDK placed content, how much, which layers held; never the content."""

    model_config = ConfigDict(extra="forbid")

    #: None for content exempt from marking: it has no record.
    record_id: str | None = Field(default=None, max_length=128)
    tool: str = Field(max_length=128)
    sink_kind: str = Field(max_length=128)
    media_type: str = Field(max_length=255)
    edit_kind: EditKind
    length_class: Literal["short", "medium", "long"]
    layers: list[LayerOutcome] = Field(max_length=32)


class VerifyBody(BaseModel):
    """A ``VerifyRequest``: a text, or a file's bytes in base64 with its media type; never both."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=1_000_000)
    file: str | None = None
    media_type: str | None = Field(default=None, max_length=255)
    signed: bool = False

    @model_validator(mode="after")
    def _one_of_them(self) -> VerifyBody:
        if (self.text is None) == (self.file is None):
            raise ValueError("Send either text or file, not both and not neither.")
        if self.file is not None and not self.media_type:
            raise ValueError("A file needs its media_type.")
        return self


def _engine(request: Request) -> ProvenanceClient:
    """The provenance engine's client; 503 with the module off."""
    client: ProvenanceClient | None = getattr(request.app.state, "provenance_client", None)
    if client is None:
        raise GatewayError(
            status_code=503,
            message="Content provenance is not switched on here.",
            error_type="provenance_unavailable",
            code="provenance_off",
        )
    return client


def _failed(exc: ProvenanceRequestError, what: str) -> GatewayError:
    """The caller's error for a call the engine did not answer as asked, with one log line for why."""
    logger.warning("The provenance service could not %s (%s): %s", what, exc.code, exc)
    if exc.code == "invalid_request":
        return GatewayError(
            status_code=400,
            message="The provenance service did not take the request.",
            error_type="invalid_request_error",
            code="invalid_provenance_request",
        )
    if exc.code == "too_large":
        return GatewayError(
            status_code=413,
            message="The content is over the provenance service's size limit.",
            error_type="invalid_request_error",
            code="too_large",
        )
    return GatewayError(
        status_code=503,
        message=f"The provenance service could not {what} right now; try again.",
        error_type="provenance_unavailable",
        code=exc.code,
    )


def _invalid(exc: ValidationError) -> JSONResponse:
    return error_response(
        status_code=400, message=validation_message(exc),
        error_type="invalid_request_error", code="validation_error",
    )


@router.get("/v1/agents/{agent}/provenance/policy")
async def agent_provenance_policy(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
) -> dict[str, Any]:
    """The marking policy the agent's SDK follows: the engine's default ``ProvenancePolicy``."""
    del caller
    client = _engine(request)
    try:
        return await client.policy()
    except ProvenanceRequestError as exc:
        raise _failed(exc, "read the policy") from exc


@router.post("/v1/agents/{agent}/provenance/records", response_model=None)
async def agent_provenance_record(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
) -> Response:
    """Sign a placement's record for the agent: a ``RecordRequest`` without ``agent``; a ``SignedRecord``."""
    _auth, agent = caller
    client = _engine(request)
    try:
        body = RecordBody.model_validate(await _get_json_payload(request))
    except ValidationError as exc:
        return _invalid(exc)
    try:
        signed = await client.sign_record(body.model_dump(mode="json"), agent=agent.id)
    except ProvenanceRequestError as exc:
        raise _failed(exc, "sign the record") from exc
    return JSONResponse(signed)


@router.post(
    "/v1/agents/{agent}/provenance/placements", status_code=status.HTTP_202_ACCEPTED, response_model=None,
)
async def agent_provenance_placement(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
) -> Response:
    """Keep the report of a placement for the agent (``PlacementReport``)."""
    _auth, agent = caller
    client = _engine(request)
    try:
        body = PlacementBody.model_validate(await _get_json_payload(request))
    except ValidationError as exc:
        return _invalid(exc)
    try:
        await client.report_placement(body.model_dump(mode="json"), agent=agent.id)
    except ProvenanceRequestError as exc:
        raise _failed(exc, "keep the placement") from exc
    return JSONResponse({"stored": True}, status_code=status.HTTP_202_ACCEPTED)


@router.get("/v1/provenance/anchors", response_model=None)
async def provenance_anchors(request: Request) -> Response:
    """The certificates and the detector's id anyone needs to check this installation's signatures offline.

    No sign-in: whoever was given marked content may check it.
    """
    client = _engine(request)
    try:
        anchors = await client.anchors()
    except ProvenanceRequestError as exc:
        raise _failed(exc, "read the anchors") from exc
    return JSONResponse(anchors, headers={"cache-control": "public, max-age=60"})


async def _verify(request: Request) -> Response:
    client = _engine(request)
    try:
        body = VerifyBody.model_validate(await _get_json_payload(request, max_bytes=VERIFY_MAX_BODY_BYTES))
    except ValidationError as exc:
        return _invalid(exc)
    try:
        result = await client.verify(body.model_dump(mode="json", exclude_none=True))
    except ProvenanceRequestError as exc:
        raise _failed(exc, "check the content") from exc
    return JSONResponse(result, headers={"cache-control": "no-store"})


@router.post("/v1/provenance/verify", response_model=None)
async def provenance_verify(
    request: Request, auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> Response:
    """Check content for this installation's marks for a signed-in user (``VerifyResult``); nothing kept."""
    del auth
    return await _verify(request)


@router.post("/v1/agents/{agent}/provenance/verify", response_model=None)
async def agent_provenance_verify(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
) -> Response:
    """Check content for this installation's marks for an agent's token (``VerifyResult``); nothing kept."""
    del caller
    return await _verify(request)
