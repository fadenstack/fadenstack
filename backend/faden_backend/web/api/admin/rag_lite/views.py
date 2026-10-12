"""FastAPI endpoints for the RAG Lite admin API.

Registered under ``/admin/rag`` when the full RAG module is disabled
and RAG Lite is enabled — providing a lightweight, pgvector-only
knowledge-base experience.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, select
from starlette import status

from faden_backend.db.dao.llm_dao import ModelDAO, ProviderDAO, RuntimeDAO
from faden_backend.db.dao.rag_lite_dao import (
    RagLiteChunkDAO,
    RagLiteCollectionDAO,
    RagLiteDocumentDAO,
    RagLiteIngestJobDAO,
)
from faden_backend.db.models.groups import Group
from faden_backend.db.models.rag_lite import (
    RagLiteCollection,
    RagLiteCollectionAccess,
    RagLiteDocument,
    RagLiteDocumentStatus,
    RagLiteIngestJob,
)
from faden_backend.db.models.users import User
from faden_backend.services.knowledge_access import (
    KnowledgeReader,
    knowledge_reader,
    may_read,
    search_scope,
    visible_collections,
)
from faden_backend.services.principals import PrincipalOption, find_principals
from faden_backend.services.rag_lite.tasks import rag_lite_ingest_task
from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values
from faden_backend.web.api.admin.rag_lite.schema import (
    RagLiteCollectionCreate,
    RagLiteCollectionDTO,
    RagLiteCollectionGrantDTO,
    RagLiteCollectionGrantIn,
    RagLiteCollectionUpdate,
    RagLiteConfigDTO,
    RagLiteDocumentDTO,
    RagLiteDocumentDetailDTO,
    RagLiteDocumentMoveRequest,
    RagLiteGraphSearchCollectionHit,
    RagLiteGraphSearchRequest,
    RagLiteGraphSearchResponse,
    RagLiteHealthResponse,
    RagLiteJobDTO,
    RagLiteJobEventDTO,
    RagLitePassageChunk,
    RagLitePassageResponse,
    RagLiteSearchRequest,
    RagLiteSearchResponse,
    RagLiteSearchResult,
    RagLiteSummaryResponse,
    RagLiteUploadResponse,
)
from faden_backend.web.api.rbac import require_permission

router = APIRouter()

#: Who reads: a sign-in with rag.search:read, or the gateway's knowledge token (services/knowledge_access.py).
Reader = Annotated[KnowledgeReader, Depends(knowledge_reader)]


async def _hidden_404(session: Any, reader: KnowledgeReader, collection_id: uuid.UUID | None, what: str) -> None:
    """A thing in a collection *reader* may not read is not there, as far as they can tell."""
    if not await may_read(session, reader, collection_id):
        raise HTTPException(status_code=404, detail=f"{what} not found")

# -----------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------


@router.get("/health", response_model=RagLiteHealthResponse)
async def rag_lite_health(
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
) -> RagLiteHealthResponse:
    return RagLiteHealthResponse()


# -----------------------------------------------------------------------
# Config (read-only — managed via Settings page)
# -----------------------------------------------------------------------


@router.get("/config", response_model=RagLiteConfigDTO)
async def get_rag_lite_config(
    _user: Annotated[User, Depends(require_permission("rag.runtime", "read"))],
    provider_dao: ProviderDAO = Depends(),
    runtime_dao: RuntimeDAO = Depends(),
    model_dao: ModelDAO = Depends(),
) -> RagLiteConfigDTO:
    from faden_backend.settings import settings  # noqa: PLC0415

    embedding_model = settings.rag_lite_embedding_model
    embedding_provider_id = settings.rag_lite_embedding_provider_id

    # Resolve the actual model name when the setting is empty
    if not embedding_model:
        try:
            pref_id = uuid.UUID(embedding_provider_id) if embedding_provider_id else None
            if pref_id:
                provider = await provider_dao.get(pref_id)
            else:
                providers = await provider_dao.list_embedding_capable()
                provider = providers[0] if providers else None

            if provider:
                if not embedding_provider_id:
                    embedding_provider_id = str(provider.id)
                # Try to get model name from a running runtime for this provider
                runtimes = await runtime_dao.list_by_provider(provider.id)
                for rt in runtimes:
                    if rt.status.value == "running":
                        model = await model_dao.get(rt.model_id)
                        if model:
                            embedding_model = model.hf_repo_id or model.display_name
                        break
                # Fallback to remote_model in capabilities
                if not embedding_model:
                    caps = provider.capabilities or {}
                    embedding_model = caps.get("remote_model", "")
        except Exception:
            pass  # best-effort — return empty if resolution fails

    return RagLiteConfigDTO(
        embedding_provider_id=embedding_provider_id,
        embedding_model=embedding_model,
        embedding_dim=settings.rag_lite_embedding_dim,
        chunk_max_tokens=settings.rag_lite_chunk_max_tokens,
        chunk_overlap_tokens=settings.rag_lite_chunk_overlap_tokens,
        file_store_root=settings.rag_lite_file_store_root,
        upload_max_file_mb=settings.rag_lite_upload_max_file_mb,
    )


# -----------------------------------------------------------------------
# Upload
# -----------------------------------------------------------------------


@router.post("/upload", response_model=RagLiteUploadResponse)
async def upload_file(
    file: UploadFile,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
    job_dao: RagLiteIngestJobDAO = Depends(),
    collection_id: uuid.UUID | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> RagLiteUploadResponse:
    rag_service = request.app.state.rag_lite_service

    # Read file bytes (enforce size limit)
    from faden_backend.settings import settings as _settings  # noqa: PLC0415

    max_mb = _settings.rag_lite_upload_max_file_mb
    file_bytes = await file.read()
    if len(file_bytes) > max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large — max {max_mb} MB.",
        )

    doc, job = await rag_service.submit_file(
        file_bytes,
        file.filename or "upload",
        collection_id,
        document_dao=document_dao,
        job_dao=job_dao,
    )

    # Committed before the task is sent: the request's session commits only
    # after this returns, and a worker that picked the task up first found no
    # document or job ("ForeignKeyViolationError" on its first event).
    await document_dao.session.commit()
    await rag_lite_ingest_task.kiq(str(doc.id), str(job.id))

    return RagLiteUploadResponse(
        document_id=doc.id,
        job_id=job.id,
        filename=doc.filename,
        doc_type=doc.doc_type,
    )


# -----------------------------------------------------------------------
# Documents
# -----------------------------------------------------------------------


@router.get("/documents", response_model=list[RagLiteDocumentDTO])
async def list_documents(
    reader: Reader,
    document_dao: RagLiteDocumentDAO = Depends(),
    collection_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[RagLiteDocumentDTO]:
    session = document_dao.session
    if collection_id is not None:
        await _hidden_404(session, reader, collection_id, "Collection")
    docs = await document_dao.list_by_collection(
        collection_id,
        limit=limit,
        offset=offset,
        scope=await search_scope(session, reader),
    )
    return [
        RagLiteDocumentDTO(
            id=d.id,
            filename=d.filename,
            doc_type=d.doc_type,
            collection_id=d.collection_id,
            size_bytes=d.size_bytes,
            chunk_count=d.chunk_count,
            status=d.status.value,
            summary=d.summary,
            created_at=d.created_at,
        )
        for d in docs
    ]


@router.get("/documents/all", response_model=list[RagLiteDocumentDTO])
async def list_all_documents(
    reader: Reader,
    document_dao: RagLiteDocumentDAO = Depends(),
    limit: int = 500,
    offset: int = 0,
) -> list[RagLiteDocumentDTO]:
    """List all documents the reader may read, regardless of collection — used for tree view."""
    scope = await search_scope(document_dao.session, reader)
    docs = await document_dao.list_all(limit=limit, offset=offset, scope=scope)
    return [
        RagLiteDocumentDTO(
            id=d.id,
            filename=d.filename,
            doc_type=d.doc_type,
            collection_id=d.collection_id,
            size_bytes=d.size_bytes,
            chunk_count=d.chunk_count,
            status=d.status.value,
            summary=d.summary,
            created_at=d.created_at,
        )
        for d in docs
    ]


@router.get("/documents/{document_id}", response_model=RagLiteDocumentDetailDTO)
async def get_document(
    document_id: uuid.UUID,
    reader: Reader,
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteDocumentDetailDTO:
    doc = await document_dao.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await _hidden_404(document_dao.session, reader, doc.collection_id, "Document")
    return RagLiteDocumentDetailDTO(
        id=doc.id,
        filename=doc.filename,
        doc_type=doc.doc_type,
        collection_id=doc.collection_id,
        size_bytes=doc.size_bytes,
        chunk_count=doc.chunk_count,
        status=doc.status.value,
        summary=doc.summary,
        created_at=doc.created_at,
        file_store_key=doc.file_store_key,
        sha256=doc.sha256,
        metadata_json=doc.metadata_json,
    )


#: The most chunks one passage returns, either side of the one asked for.
_PASSAGE_MAX_AROUND = 5


@router.get("/documents/{document_id}/passage", response_model=RagLitePassageResponse)
async def get_passage(
    document_id: uuid.UUID,
    reader: Reader,
    chunk: int = 0,
    around: int = 2,
    document_dao: RagLiteDocumentDAO = Depends(),
    chunk_dao: RagLiteChunkDAO = Depends(),
) -> RagLitePassageResponse:
    """The text of a document around one chunk.

    A search returns single chunks; the model's ``knowledge_open`` tool reads
    what surrounds one, to follow a hit into its section.
    """
    doc = await document_dao.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await _hidden_404(document_dao.session, reader, doc.collection_id, "Document")
    around = max(0, min(around, _PASSAGE_MAX_AROUND))
    chunks = await chunk_dao.passage(document_id, first=max(0, chunk - around), last=chunk + around)
    return RagLitePassageResponse(
        document_id=doc.id,
        filename=doc.filename,
        collection_id=doc.collection_id,
        chunk_count=doc.chunk_count or 0,
        chunks=[RagLitePassageChunk(chunk_index=c.chunk_index, text=c.chunk_text) for c in chunks],
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
    chunk_dao: RagLiteChunkDAO = Depends(),
    request: Request = None,  # type: ignore[assignment]
) -> None:
    rag_service = request.app.state.rag_lite_service
    deleted = await rag_service.delete_document(
        document_id,
        document_dao=document_dao,
        chunk_dao=chunk_dao,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")


@router.post("/documents/{document_id}/retry", status_code=200)
async def retry_document(
    document_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
    chunk_dao: RagLiteChunkDAO = Depends(),
    job_dao: RagLiteIngestJobDAO = Depends(),
) -> dict:
    doc = await document_dao.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status.value not in ("error", "pending"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry document in '{doc.status.value}' state.",
        )

    # Clear old chunks from any partial previous run
    await chunk_dao.delete_by_document(document_id)

    # Reset document status
    await document_dao.update_status(
        document_id,
        RagLiteDocumentStatus.PENDING,
        chunk_count=0,
    )

    # Create a new ingest job and dispatch -- committed first, as on upload.
    job = await job_dao.create(document_id)
    await job_dao.session.commit()
    await rag_lite_ingest_task.kiq(str(document_id), str(job.id))

    return {"document_id": str(document_id), "job_id": str(job.id)}


# -----------------------------------------------------------------------
# Search
# -----------------------------------------------------------------------


@router.post("/search", response_model=RagLiteSearchResponse)
async def search(
    body: RagLiteSearchRequest,
    reader: Reader,
    chunk_dao: RagLiteChunkDAO = Depends(),
    request: Request = None,  # type: ignore[assignment]
) -> RagLiteSearchResponse:
    rag_service = request.app.state.rag_lite_service

    # Resolve embedding client at request time
    from faden_backend.services.rag_lite.embedding import EmbeddingClient  # noqa: PLC0415
    from faden_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from faden_backend.settings import settings  # noqa: PLC0415

    # As they are now, not as this process last heard: with several workers,
    # a settings change reaches only the one that handled it.
    await refresh_runtime_values(chunk_dao.session, prefix="rag_lite.")
    crypto = SettingsCrypto.from_settings()
    pref_id_str = settings.rag_lite_embedding_provider_id
    pref_id = uuid.UUID(pref_id_str) if pref_id_str else None

    session = chunk_dao.session
    # The app's client: its connections to the embedding and reranking
    # servers stay open from one search to the next.
    http_client = getattr(request.app.state, "http_client", None)
    embedding_client = await EmbeddingClient.auto_detect(
        session,
        preferred_provider_id=pref_id,
        model_override=settings.rag_lite_embedding_model or None,
        dim=settings.rag_lite_embedding_dim,
        crypto=crypto,
        http_client=http_client,
    )

    from faden_backend.services.rag_lite.rerank import RerankClient  # noqa: PLC0415

    hybrid = settings.rag_lite_hybrid_search if body.mode is None else body.mode == "hybrid"
    reranker = (
        await RerankClient.from_settings(session, crypto=crypto, http_client=http_client)
        if body.rerank is not False else None
    )
    results = await rag_service.search(
        body.query,
        chunk_dao=chunk_dao,
        embedding_client=embedding_client,
        top_k=body.top_k,
        collection_ids=body.collection_ids,
        hybrid=hybrid,
        reranker=reranker,
        candidates=settings.rag_lite_rerank_candidates,
        scope=await search_scope(session, reader),
    )
    return RagLiteSearchResponse(
        query=body.query,
        results=[
            RagLiteSearchResult(
                chunk_text=r["chunk_text"],
                document_id=uuid.UUID(r["document_id"]),
                filename=r["filename"],
                chunk_index=r["chunk_index"],
                score=r["score"],
            )
            for r in results
        ],
    )


# -----------------------------------------------------------------------
# Collections
# -----------------------------------------------------------------------


@router.post("/collections", response_model=RagLiteCollectionDTO, status_code=201)
async def create_collection(
    body: RagLiteCollectionCreate,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
) -> RagLiteCollectionDTO:
    grants = await _checked_grants(collection_dao.session, body.access, body.grants)
    col = await collection_dao.create(
        body.name, body.description, parent_id=body.parent_id,
    )
    await collection_dao.set_access(col, body.access, grants)
    return await _collection_dto(collection_dao, col, 0, manager=True)


@router.get("/collections", response_model=list[RagLiteCollectionDTO])
async def list_collections(
    reader: Reader,
    collection_dao: RagLiteCollectionDAO = Depends(),
) -> list[RagLiteCollectionDTO]:
    rows = await collection_dao.list_with_doc_counts()
    manager = reader.sees_all and reader.only is None
    if not manager:
        visible, _total = await visible_collections(collection_dao.session, reader)
        rows = [(c, count) for c, count in rows if c.id in visible]
    grants = await collection_dao.grants_of([c.id for c, _ in rows]) if manager else {}
    labels = await _labels(collection_dao.session, [g for gs in grants.values() for g in gs])
    return [
        _dto(c, count, grants.get(c.id, []), labels)
        for c, count in rows
    ]


@router.patch("/collections/{collection_id}", response_model=RagLiteCollectionDTO)
async def update_collection(
    collection_id: uuid.UUID,
    body: RagLiteCollectionUpdate,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
) -> RagLiteCollectionDTO:
    access = body.access
    grants = None
    if access is not None or body.grants is not None:
        current = await collection_dao.get(collection_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Collection not found")
        access = access or current.access
        grants = await _checked_grants(collection_dao.session, access, body.grants)
    # Only what was sent: a summary edit sent no parent and was refused, and a rename cleared the summary.
    sent = body.model_fields_set
    col = await collection_dao.update(
        collection_id,
        name=body.name,
        description=body.description if "description" in sent else ...,
        parent_id=body.parent_id if "parent_id" in sent else ...,
    )
    if col is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    if access is not None:
        await collection_dao.set_access(col, access, grants)
    else:
        await collection_dao.session.flush()
        await collection_dao.session.refresh(col)  # what the database set (updated_at), read now
    count = await collection_dao.session.scalar(
        select(func.count(RagLiteDocument.id)).where(RagLiteDocument.collection_id == col.id),
    )
    return await _collection_dto(collection_dao, col, int(count or 0), manager=True)


async def _checked_grants(
    session: Any, access: str, grants: list[RagLiteCollectionGrantIn] | None,
) -> list[tuple[str, uuid.UUID]] | None:
    """The grants as ``(type, id)``, every one a person or group that exists; none for an open collection."""
    if access == RagLiteCollectionAccess.EVERYONE.value:
        return []  # open to everyone: nobody needs a grant, and old ones would only confuse
    if grants is None:
        return None
    pairs = [(g.principal_type, g.principal_id) for g in grants]
    users = {i for t, i in pairs if t == "user"}
    groups = {i for t, i in pairs if t == "group"}
    found_users = set((await session.execute(select(User.id).where(User.id.in_(users)))).scalars()) if users else set()
    found_groups = (
        set((await session.execute(select(Group.id).where(Group.id.in_(groups)))).scalars()) if groups else set()
    )
    missing = (users - found_users) | (groups - found_groups)
    if missing:
        raise HTTPException(
            status_code=422, detail=f"No such person or group: {', '.join(sorted(str(m) for m in missing))}",
        )
    return pairs


async def _labels(session: Any, grants: list[Any]) -> dict[tuple[str, uuid.UUID], str]:
    """The e-mail address of each person and the name of each group in *grants*."""
    users = {g.principal_id for g in grants if g.principal_type == "user"}
    groups = {g.principal_id for g in grants if g.principal_type == "group"}
    labels: dict[tuple[str, uuid.UUID], str] = {}
    if users:
        for uid, email in (await session.execute(select(User.id, User.email).where(User.id.in_(users)))).all():
            labels[("user", uid)] = email
    if groups:
        for gid, name in (await session.execute(select(Group.id, Group.name).where(Group.id.in_(groups)))).all():
            labels[("group", gid)] = name
    return labels


def _dto(
    col: RagLiteCollection, count: int, grants: list[Any], labels: dict[tuple[str, uuid.UUID], str],
) -> RagLiteCollectionDTO:
    return RagLiteCollectionDTO(
        id=col.id,
        name=col.name,
        description=col.description,
        parent_id=col.parent_id,
        document_count=count,
        access=col.access,
        grants=[
            RagLiteCollectionGrantDTO(
                principal_type=g.principal_type,
                principal_id=g.principal_id,
                label=labels.get((g.principal_type, g.principal_id)),
            )
            for g in grants
        ],
        created_at=col.created_at,
        updated_at=col.updated_at,
    )


async def _collection_dto(
    collection_dao: RagLiteCollectionDAO, col: RagLiteCollection, count: int, *, manager: bool,
) -> RagLiteCollectionDTO:
    grants = (await collection_dao.grants_of([col.id])).get(col.id, []) if manager else []
    return _dto(col, count, grants, await _labels(collection_dao.session, grants))


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
) -> None:
    deleted = await collection_dao.delete(collection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Collection not found")


@router.get("/principals", response_model=list[PrincipalOption])
async def find_collection_principals(
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
    q: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    ids: Annotated[str | None, Query(max_length=4000)] = None,
) -> list[PrincipalOption]:
    """The people and groups a collection can be opened to (as for skills and agents)."""
    return await find_principals(collection_dao.session, q=q, ids=ids)


# -----------------------------------------------------------------------
# Jobs
# -----------------------------------------------------------------------


def _job_to_dto(job: RagLiteIngestJob) -> RagLiteJobDTO:
    events = [
        RagLiteJobEventDTO(
            id=e.id,
            event_type=e.event_type.value,
            message=e.message,
            created_at=e.created_at,
        )
        for e in (job.events or [])
    ]
    return RagLiteJobDTO(
        id=job.id,
        document_id=job.document_id,
        status=job.status.value,
        error_message=job.error_message,
        stats_json=job.stats_json,
        created_at=job.created_at,
        updated_at=job.updated_at,
        events=events,
    )


@router.get("/jobs", response_model=list[RagLiteJobDTO])
async def list_jobs(
    reader: Reader,
    job_dao: RagLiteIngestJobDAO = Depends(),
    limit: int = 50,
) -> list[RagLiteJobDTO]:
    jobs = await job_dao.list_recent(limit=limit)
    scope = await search_scope(job_dao.session, reader)
    if scope is not None and jobs:
        readable = set(
            (
                await job_dao.session.execute(
                    select(RagLiteDocument.id).where(
                        RagLiteDocument.id.in_({j.document_id for j in jobs}), scope.documents(),
                    ),
                )
            ).scalars(),
        )
        jobs = [j for j in jobs if j.document_id in readable]
    return [_job_to_dto(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=RagLiteJobDTO)
async def get_job(
    job_id: uuid.UUID,
    reader: Reader,
    job_dao: RagLiteIngestJobDAO = Depends(),
) -> RagLiteJobDTO:
    job = await job_dao.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    doc = await job_dao.session.get(RagLiteDocument, job.document_id)
    await _hidden_404(job_dao.session, reader, doc.collection_id if doc else None, "Job")
    return _job_to_dto(job)


# -----------------------------------------------------------------------
# Document move / summary
# -----------------------------------------------------------------------


@router.patch("/documents/{document_id}/move", response_model=RagLiteDocumentDTO)
async def move_document(
    document_id: uuid.UUID,
    body: RagLiteDocumentMoveRequest,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteDocumentDTO:
    """Move a document to a different collection (or root)."""
    doc = await document_dao.move_to_collection(document_id, body.collection_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return RagLiteDocumentDTO(
        id=doc.id,
        filename=doc.filename,
        doc_type=doc.doc_type,
        collection_id=doc.collection_id,
        size_bytes=doc.size_bytes,
        chunk_count=doc.chunk_count,
        status=doc.status.value,
        summary=doc.summary,
        created_at=doc.created_at,
    )


@router.patch("/documents/{document_id}/summary")
async def update_document_summary(
    document_id: uuid.UUID,
    body: RagLiteSummaryResponse,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteSummaryResponse:
    """Update a document's summary."""
    doc = await document_dao.update_summary(document_id, body.summary)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return RagLiteSummaryResponse(summary=doc.summary or "")


# -----------------------------------------------------------------------
# AI Summary Generation
# -----------------------------------------------------------------------


async def _get_completion_client(session: Any) -> Any:
    """Resolve a CompletionClient from available providers."""
    from faden_backend.services.rag_lite.completion import CompletionClient  # noqa: PLC0415
    from faden_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from faden_backend.settings import settings  # noqa: PLC0415

    crypto = SettingsCrypto.from_settings()
    return await CompletionClient.auto_detect(session, crypto=crypto)


@router.post(
    "/collections/{collection_id}/generate-summary",
    response_model=RagLiteSummaryResponse,
)
async def generate_collection_summary(
    collection_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteSummaryResponse:
    """Generate an AI summary for a collection based on its documents."""
    col = await collection_dao.get(collection_id)
    if col is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    docs = await document_dao.list_by_collection(collection_id, limit=50)
    doc_names = [d.filename for d in docs]
    doc_summaries = [d.summary for d in docs]

    try:
        client = await _get_completion_client(collection_dao.session)
        summary = await client.generate_collection_summary(
            col.name, doc_names, doc_summaries,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Persist the generated summary
    await collection_dao.update(collection_id, description=summary)
    return RagLiteSummaryResponse(summary=summary)


@router.post(
    "/documents/{document_id}/generate-summary",
    response_model=RagLiteSummaryResponse,
)
async def generate_document_summary(
    document_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteSummaryResponse:
    """Generate an AI summary for a document based on its content."""
    doc = await document_dao.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        client = await _get_completion_client(document_dao.session)
        summary = await client.generate_document_summary(
            doc.filename, doc.content_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Persist the generated summary
    await document_dao.update_summary(document_id, summary)
    return RagLiteSummaryResponse(summary=summary)


# -----------------------------------------------------------------------
# Graph-RAG Search (two-level)
# -----------------------------------------------------------------------


@router.post("/search/graph", response_model=RagLiteGraphSearchResponse)
async def graph_search(
    body: RagLiteGraphSearchRequest,
    reader: Reader,
    collection_dao: RagLiteCollectionDAO = Depends(),
    chunk_dao: RagLiteChunkDAO = Depends(),
    request: Request = None,  # type: ignore[assignment]
) -> RagLiteGraphSearchResponse:
    """Two-level graph-RAG search.

    Level 1: Embed the query, compare against collection summaries to
    find the most relevant collections.

    Level 2: Run chunk-level vector search within those collections.
    """
    from faden_backend.services.rag_lite.embedding import EmbeddingClient  # noqa: PLC0415
    from faden_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from faden_backend.settings import settings  # noqa: PLC0415

    # As they are now, not as this process last heard: with several workers,
    # a settings change reaches only the one that handled it.
    await refresh_runtime_values(chunk_dao.session, prefix="rag_lite.")
    crypto = SettingsCrypto.from_settings()
    pref_id_str = settings.rag_lite_embedding_provider_id
    pref_id = uuid.UUID(pref_id_str) if pref_id_str else None

    session = chunk_dao.session
    embedding_client = await EmbeddingClient.auto_detect(
        session,
        preferred_provider_id=pref_id,
        model_override=settings.rag_lite_embedding_model or None,
        dim=settings.rag_lite_embedding_dim,
        crypto=crypto,
    )

    # Level 1: Find best collections by embedding summaries -- of those the reader may read
    scope = await search_scope(session, reader)
    rows = await collection_dao.list_with_doc_counts()
    if scope is not None:
        rows = [(c, count) for c, count in rows if c.id in scope.collections]
    cols_with_desc = [
        (c, count) for c, count in rows if c.description and c.description.strip()
    ]

    collection_hits: list[RagLiteGraphSearchCollectionHit] = []
    target_collection_ids: list[uuid.UUID] = []

    if cols_with_desc:
        # Embed query + all collection descriptions in one batch
        texts_to_embed = [body.query] + [c.description for c, _ in cols_with_desc]  # type: ignore[misc]
        all_vectors = await embedding_client.embed_texts(texts_to_embed)
        query_vec = all_vectors[0]

        # Compute cosine similarity for each collection
        scored: list[tuple[Any, int, float]] = []
        for i, (col, count) in enumerate(cols_with_desc):
            col_vec = all_vectors[i + 1]
            sim = _cosine_similarity(query_vec, col_vec)
            scored.append((col, count, sim))

        scored.sort(key=lambda x: x[2], reverse=True)
        top = scored[: body.top_k_collections]

        for col, count, sim in top:
            collection_hits.append(
                RagLiteGraphSearchCollectionHit(
                    collection_id=col.id,
                    collection_name=col.name,
                    score=sim,
                ),
            )
            target_collection_ids.append(col.id)

    # Level 2: Search chunks within the matched collections
    if target_collection_ids:
        results = await chunk_dao.search_similar(
            query_vector=query_vec,
            top_k=body.top_k_chunks,
            collection_ids=target_collection_ids,
            scope=scope,
        )
    else:
        # No collections matched — search all
        query_vecs = await embedding_client.embed_texts([body.query])
        results = await chunk_dao.search_similar(
            query_vector=query_vecs[0],
            top_k=body.top_k_chunks,
            scope=scope,
        )

    return RagLiteGraphSearchResponse(
        query=body.query,
        collection_hits=collection_hits,
        results=[
            RagLiteSearchResult(
                chunk_text=r["chunk_text"],
                document_id=uuid.UUID(r["document_id"]),
                filename=r["filename"],
                chunk_index=r["chunk_index"],
                score=r["score"],
            )
            for r in results
        ],
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    import math  # noqa: PLC0415

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
