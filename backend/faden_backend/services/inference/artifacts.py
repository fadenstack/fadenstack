"""Inference model artifact readiness coordination and lifecycle service (Phase 4C, WI-3).

Coordinates per-node model artifact readiness for inference environments:
- Resolves cache manifests and canonical manifest digests.
- Reconciles per-node ModelAvailability rows (MISSING / STALE / READY / FAILED).
- Evaluates artifact readiness across eligible environment nodes.
- Dispatches idempotent SYNC_MODEL commands via NodeCommandGateway.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO, ModelAvailabilityDAO
from faden_backend.db.models.inference import (
    InferenceEnvironment,
    ModelAvailability,
    ModelAvailabilityStatus,
)
from faden_backend.db.models.node_control import InfraNode, NodeCommandType
from faden_backend.settings import settings
from faden_backend.services.inference.drivers.ray.commands import NodeCommandGateway
from faden_backend.services.llm.artifacts import (
    build_cache_manifest,
    build_model_sync_payload,
    model_sync_carries_files,
    model_cache_dir,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from faden_backend.db.models.llm import LLMModel

log = logging.getLogger(__name__)


class ArtifactReadiness(BaseModel):
    """Report of model artifact readiness across an environment's nodes."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    desired_revision: str | None = None
    manifest_sha256: str | None = None
    ready_node_ids: list[str] = Field(default_factory=list)
    pending_node_ids: list[str] = Field(default_factory=list)
    failed_node_ids: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    root_paths: dict[str, str] = Field(default_factory=dict)  # node_id -> host root path
    all_ready: bool = False
    #: Something under way that will unblock this by itself -- the server
    #: downloading the model, say. Not a blocker: nothing is wrong, the
    #: deployment only has to wait, and saying so is the whole point.
    waiting_on: str | None = None


# A node whose sync already failed is retried, but not on every pass: the
# reconciler runs continuously and re-issuing immediately would hammer a node
# that is genuinely broken.
_FAILED_RETRY_BACKOFF_SEC = 300.0

# States that mean "a sync for this digest is already under way".  Re-marking
# these would destroy the progress the agent is reporting.
# How long a PENDING/SYNCING row may go untouched before it is assumed dead.
# Comfortably longer than a large model transfer, because cancelling a real
# sync costs a re-download; short enough that a wedged node recovers on its
# own rather than needing someone to notice.
_IN_FLIGHT_STALE_SEC = 3600.0

_IN_FLIGHT_STATUSES = frozenset({
    ModelAvailabilityStatus.PENDING.value,
    ModelAvailabilityStatus.SYNCING.value,
})


def _default_revision(refs: list[dict[str, Any]]) -> str | None:
    """Pick the revision a sync will actually land on.

    Mirrors the agent's own precedence (``main`` -> ``master`` -> first ref).
    Taking ``refs[0]`` instead meant the backend recorded the alphabetically
    first ref - ``dev`` in a cache holding ``dev`` and ``main`` - while the
    agent synced ``main``.
    """
    by_name = {str(r.get("name")): r.get("commit") for r in refs if isinstance(r, dict)}
    for preferred in ("main", "master"):
        if by_name.get(preferred):
            return str(by_name[preferred])
    for commit in by_name.values():
        if commit:
            return str(commit)
    return None


def is_ready_for(row: ModelAvailability | None, desired_digest: str | None) -> bool:
    """Check if *row* represents a verified, loadable artifact for *desired_digest*."""
    if row is None:
        return False
    if row.status != ModelAvailabilityStatus.READY.value:
        return False
    if not row.root_path:
        return False
    if desired_digest and row.manifest_sha256 != desired_digest:
        return False
    return True


def _machines_downloading(model: LLMModel, readiness: ArtifactReadiness, rows: dict[Any, Any]) -> str:
    """What a deployment waits on while its machines download the model from the Hub."""
    nodes = len(readiness.ready_node_ids) + len(readiness.pending_node_ids)
    progress = [
        float(getattr(row, "progress", 0) or 0)
        for row in rows.values()
        if getattr(row, "status", None) == ModelAvailabilityStatus.SYNCING.value
    ]
    done = (sum(progress) + 100.0 * len(readiness.ready_node_ids)) / nodes if nodes else 0.0
    share = f" ({done:.0f}%)" if done else ""
    return (
        f"The machines are downloading {model.hf_repo_id} from Hugging Face themselves{share}; "
        f"{len(readiness.ready_node_ids)} of {nodes} have it."
    )


class ModelArtifactCoordinator:
    """Coordinates model artifact synchronization and readiness for inference environments."""

    def __init__(self, session: AsyncSession, *, gateway: Any = None) -> None:
        self.session = session
        self._dao = ModelAvailabilityDAO(session)
        self._gateway = NodeCommandGateway(gateway) if gateway is not None else None

    async def eligible_nodes(self, environment: InferenceEnvironment) -> list[InfraNode]:
        """Resolve member nodes eligible for scheduling workloads in *environment*.

        Filters out nodes that are in maintenance, draining, or ineligible.
        """
        nodes = await EnvironmentNodeDAO(self.session).machines(environment.id)

        eligible: list[InfraNode] = []
        for node in nodes:
            if not node.scheduler_eligible:
                continue
            if node.maintenance_mode:
                continue
            if node.draining:
                continue
            status = str(node.status or "").lower()
            if status in ("offline", "disabled"):
                continue
            eligible.append(node)
        return eligible

    async def evaluate(
        self,
        *,
        model: LLMModel,
        environment: InferenceEnvironment,
        persist: bool = True,
    ) -> ArtifactReadiness:
        """Evaluate artifact readiness for *model* across eligible nodes in *environment*.

        Reconciles ModelAvailability rows (marks MISSING or STALE when detected).
        Does NOT issue commands.

        ``persist=False`` reports the same readiness without writing any row, so
        a read-only caller does not mutate state just by looking at it.
        """
        from faden_backend.services.inference import machine_downloads  # noqa: PLC0415

        desired_revision = model.hf_revision
        manifest_sha256: str | None = None

        # A cluster whose machines download the model themselves holds what
        # the Hub has, not the server's copy: that copy -- if the server has
        # one, or half of one -- says nothing about theirs.
        if model.hf_repo_id and not machine_downloads.enabled(environment):
            m_dir = model_cache_dir(model.hf_repo_id)
            if m_dir is not None:
                manifest = build_cache_manifest(m_dir)
                manifest_sha256 = manifest.get("manifest_sha256")
                if not desired_revision:
                    desired_revision = _default_revision(manifest.get("refs") or [])

        nodes = await self.eligible_nodes(environment)
        blockers: list[str] = []
        if not nodes:
            blockers.append("No eligible scheduler nodes in environment")

        node_ids = [n.id for n in nodes]
        rows = await self._dao.list_for_nodes(model.id, node_ids)
        row_by_node = {r.node_id: r for r in rows}

        ready_node_ids: list[str] = []
        pending_node_ids: list[str] = []
        failed_node_ids: list[str] = []
        root_paths: dict[str, str] = {}

        for node in nodes:
            node_str = str(node.id)
            row = row_by_node.get(node.id)
            if row is None:
                # Probed and absent -> record MISSING
                if persist:
                    await self._dao.mark(
                        model.id,
                        node.id,
                        ModelAvailabilityStatus.MISSING,
                        status_message="Artifact missing from node",
                    )
                pending_node_ids.append(node_str)
            elif is_ready_for(row, manifest_sha256):
                ready_node_ids.append(node_str)
                if row.root_path:
                    root_paths[node_str] = row.root_path
            elif (
                row.status == ModelAvailabilityStatus.READY.value
                and manifest_sha256
                and row.manifest_sha256 != manifest_sha256
            ):
                # Manifest digest differs from current desired digest -> STALE
                if persist:
                    await self._dao.mark(
                        model.id,
                        node.id,
                        ModelAvailabilityStatus.STALE,
                        status_message=(
                            f"Artifact digest {row.manifest_sha256[:12] if row.manifest_sha256 else 'none'} "
                            f"differs from desired {manifest_sha256[:12]}"
                        ),
                    )
                pending_node_ids.append(node_str)
            elif row.status == ModelAvailabilityStatus.FAILED.value:
                failed_node_ids.append(node_str)
                blockers.append(
                    f"Artifact sync failed on node {node.host or node_str}: {row.status_message or 'unknown error'}"
                )
            else:
                # SYNCING, PENDING, MISSING, STALE, UNKNOWN
                pending_node_ids.append(node_str)

        all_ready = len(ready_node_ids) == len(nodes) and len(nodes) > 0

        return ArtifactReadiness(
            model_id=str(model.id),
            desired_revision=desired_revision,
            manifest_sha256=manifest_sha256,
            ready_node_ids=ready_node_ids,
            pending_node_ids=pending_node_ids,
            failed_node_ids=failed_node_ids,
            blockers=blockers,
            root_paths=root_paths,
            all_ready=all_ready,
        )

    @staticmethod
    def _sync_in_flight(
        row: ModelAvailability | None,
        desired_digest: str | None,
        now: datetime,
    ) -> bool:
        """Is a sync for *desired_digest* already under way on this node?

        PENDING/SYNCING rows for the same digest are left untouched so their
        progress survives the reconcile pass.  A FAILED row is retried, but not
        on every pass: the reconciler runs continuously, and re-issuing
        immediately would hammer a node that is genuinely broken.  Both cases
        previously fell into the same "pending" bucket, so an in-flight sync was
        reset to PENDING on every pass and a failed one was never retried at all.
        """
        if row is None:
            return False
        if desired_digest and row.manifest_sha256 != desired_digest:
            return False  # different digest: this one needs re-issuing
        if row.status in _IN_FLIGHT_STATUSES:
            # "In flight" is a claim with an expiry date.  A sync whose agent
            # died -- or whose command was reaped -- leaves the row SYNCING
            # forever, and because this branch skips the node, it is then
            # never retried: the artifact never becomes ready and whatever
            # waits on it waits for good.  A row that has not been touched in
            # a long time is not in flight, whatever it says.
            updated = row.updated_at
            if updated is None:
                return True
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            if (now - updated).total_seconds() < _IN_FLIGHT_STALE_SEC:
                return True
            log.warning(
                "Model availability row for node %s has been %s since %s with no "
                "progress; treating it as abandoned and re-issuing.",
                row.node_id, row.status, updated,
            )
            return False
        if row.status == ModelAvailabilityStatus.FAILED.value:
            updated = row.updated_at
            if updated is None:
                return False
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            return (now - updated).total_seconds() < _FAILED_RETRY_BACKOFF_SEC
        return False

    async def _payload_to_send(
        self,
        model: LLMModel,
        readiness: ArtifactReadiness,
        *,
        fetch_to_server: bool,
    ) -> dict[str, Any] | None:
        """The SYNC_MODEL payload for *model*, or None with *readiness* saying why not."""
        from faden_backend.services.inference.server_copy import (  # noqa: PLC0415
            download_under_way,
            ensure_server_copy,
            incomplete_server_copy,
        )

        # Halfway through a download the folder already holds files, and
        # sending them had both machines refuse "Invalid blob hash" for a
        # partial blob, attempt after attempt, until the download finished
        # (the release VM, Qwen3-0.6B). Wait for it instead.
        downloading = await download_under_way(self.session, model)
        if downloading is not None:
            readiness.waiting_on = downloading
            return None

        # A download that stopped is no copy either, whatever arrived before
        # it did: send it and every machine fails on the files that are not
        # there. One that was interrupted is resumed -- something is waiting
        # for the model -- while a new download is started only by a caller
        # that may fetch to the server.
        incomplete = await incomplete_server_copy(self.session, model)
        if incomplete is not None:
            copy = await ensure_server_copy(self.session, model, resume_only=not fetch_to_server)
            if copy.waiting is not None:
                readiness.waiting_on = copy.waiting
            else:
                readiness.blockers.append(copy.blocker or incomplete)
            return None

        sync_payload = build_model_sync_payload(model, source="sync_from_server")
        if sync_payload is None:
            readiness.blockers.append(f"Model {model.id} has no repository or cache manifest to sync")
            return None

        if not model_sync_carries_files(sync_payload):
            # Nothing to send because the server does not have it. The
            # deployment names the model, so fetch it rather than stall.
            copy = (
                await ensure_server_copy(self.session, model)
                if fetch_to_server
                else None
            )
            if copy is None:
                pass  # falls through to the refusal below
            elif copy.waiting is not None:
                readiness.waiting_on = copy.waiting
                return None
            elif copy.blocker is not None:
                readiness.blockers.append(copy.blocker)
                return None
            # Refusing here is the whole point.  Sending it would have every
            # node reject it and be marked FAILED, which reads as "both
            # machines are broken" when the machines are fine and this server
            # simply has no copy to send.  Name the server, and say what would
            # fix it.
            store = settings.model_store_root
            readiness.blockers.append(
                f"This server has no local copy of {model.hf_repo_id} to send "
                f"(looked in {store}). Import or download the model here first, "
                f"then sync it to the cluster."
            )
            log.warning(
                "Not issuing SYNC_MODEL for %s: no local files under %s",
                model.hf_repo_id, store,
            )
            return None
        return sync_payload

    async def ensure(
        self,
        *,
        model: LLMModel,
        environment: InferenceEnvironment,
        gateway: Any = None,
        fetch_to_server: bool = False,
    ) -> ArtifactReadiness:
        """Evaluate readiness and issue SYNC_MODEL commands for non-ready nodes.

        ``fetch_to_server`` starts the server-side download of a model the
        server lacks. A cluster's machines cannot fetch one themselves:
        SYNC_MODEL carries files from this server, and the runtime is
        air-gapped. So a deployment asks for it unless its environment lets
        the engine download (``allow_remote_fetch``).

        Idempotent: does not re-issue commands already in flight for the desired digest.
        """
        from faden_backend.services.inference import machine_downloads  # noqa: PLC0415

        readiness = await self.evaluate(model=model, environment=environment)
        if readiness.all_ready:
            return readiness

        on_machines = machine_downloads.enabled(environment)
        if on_machines:
            # Each machine downloads the model from the Hub itself; the server
            # neither downloads it nor sends it.
            sync_payload = machine_downloads.sync_payload(model)
            if sync_payload is None:
                readiness.blockers.append(
                    f"{model.display_name} has no Hugging Face repository for the machines to download."
                )
                return readiness
        else:
            sync_payload = await self._payload_to_send(model, readiness, fetch_to_server=fetch_to_server)
            if sync_payload is None:
                return readiness

        gw = (
            gateway
            if isinstance(gateway, NodeCommandGateway)
            else (NodeCommandGateway(gateway) if gateway is not None else self._gateway)
        )
        if gw is None:
            readiness.blockers.append("No node command gateway available to issue SYNC_MODEL")
            return readiness

        target_nodes = set(readiness.pending_node_ids) | set(readiness.failed_node_ids)
        digest_tag = (
            f"hf:{sync_payload['revision']}" if on_machines else readiness.manifest_sha256 or "latest"
        )

        # Rows are re-read here so an in-flight sync can be recognised.  The
        # readiness report only carries node ids, and re-marking a node that is
        # already SYNCING would overwrite the progress the agent is streaming
        # back - the row would flap PENDING/SYNCING for the whole transfer.
        node_uuids: list[uuid.UUID] = []
        for node_id_str in target_nodes:
            try:
                node_uuids.append(uuid.UUID(node_id_str))
            except ValueError:
                continue
        rows_now = {r.node_id: r for r in await self._dao.list_for_nodes(model.id, node_uuids)}
        now = datetime.now(tz=UTC)

        for node_uuid in node_uuids:
            node_id_str = str(node_uuid)
            row = rows_now.get(node_uuid)

            if self._sync_in_flight(row, readiness.manifest_sha256, now):
                log.debug(
                    "Artifact sync already in flight for model %s on node %s; leaving it alone",
                    model.id, node_id_str,
                )
                continue

            idem_key = f"artifact:{model.id}:{digest_tag}:{node_id_str}"

            # Mark state machine PENDING prior to dispatch
            await self._dao.mark(
                model.id,
                node_uuid,
                ModelAvailabilityStatus.PENDING,
                manifest_sha256=readiness.manifest_sha256,
                revision=readiness.desired_revision,
                status_message="Sync command queued",
            )

            try:
                await gw.issue(
                    node_id=node_uuid,
                    command_type=NodeCommandType.SYNC_MODEL.value,
                    payload={"model_sync": sync_payload},
                    idempotency_key=idem_key,
                    timeout_sec=1800,
                )
            except Exception as exc:
                log.warning("Failed issuing SYNC_MODEL to node %s: %s", node_id_str, exc)
                readiness.blockers.append(f"Failed issuing SYNC_MODEL to node {node_id_str}: {exc}")

        if on_machines and not readiness.failed_node_ids and not readiness.blockers:
            readiness.waiting_on = _machines_downloading(model, readiness, rows_now)
        return readiness
