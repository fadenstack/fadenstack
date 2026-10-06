"""Pydantic DTOs for RAG Lite admin endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# -----------------------------------------------------------------------
# Upload
# -----------------------------------------------------------------------


class RagLiteUploadResponse(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    filename: str
    doc_type: str
    status: str = "pending"
    message: str = "File accepted — processing in background."


# -----------------------------------------------------------------------
# Documents
# -----------------------------------------------------------------------


class RagLiteDocumentDTO(BaseModel):
    id: uuid.UUID
    filename: str
    doc_type: str
    collection_id: uuid.UUID | None
    size_bytes: int
    chunk_count: int
    status: str
    summary: str | None = None
    created_at: datetime


class RagLiteDocumentDetailDTO(RagLiteDocumentDTO):
    file_store_key: str | None = None
    sha256: str
    metadata_json: dict | None = None


# -----------------------------------------------------------------------
# Search
# -----------------------------------------------------------------------


class RagLiteSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    collection_ids: list[uuid.UUID] | None = None
    #: ``hybrid`` (keyword and vector) or ``dense`` (vector alone);
    #: default: the RAG Lite setting.
    mode: Literal["hybrid", "dense"] | None = None
    #: Re-score the candidates with the reranker; default: when one is set.
    rerank: bool | None = None


class RagLiteSearchResult(BaseModel):
    chunk_text: str
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    score: float


class RagLiteSearchResponse(BaseModel):
    results: list[RagLiteSearchResult]
    query: str


class RagLitePassageChunk(BaseModel):
    chunk_index: int
    text: str


class RagLitePassageResponse(BaseModel):
    """A stretch of one document: what a model reads after a search hit."""

    document_id: uuid.UUID
    filename: str
    collection_id: uuid.UUID | None
    chunk_count: int
    chunks: list[RagLitePassageChunk]


# -----------------------------------------------------------------------
# Collections
# -----------------------------------------------------------------------


class RagLiteCollectionGrantIn(BaseModel):
    """A person or group a restricted collection is open to."""

    principal_type: Literal["user", "group"]
    principal_id: uuid.UUID


class RagLiteCollectionGrantDTO(RagLiteCollectionGrantIn):
    #: The person's e-mail address or the group's name.
    label: str | None = None


class RagLiteCollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    #: Everyone, or only the people and groups in ``grants`` (a restricted collection hides what is below it).
    access: Literal["everyone", "restricted"] = "everyone"
    grants: list[RagLiteCollectionGrantIn] = Field(default_factory=list)


class RagLiteCollectionUpdate(BaseModel):
    """What changes: only the fields sent (``None`` for ``parent_id`` moves it to the top)."""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    access: Literal["everyone", "restricted"] | None = None
    #: Replaces the grants when given.
    grants: list[RagLiteCollectionGrantIn] | None = None


class RagLiteCollectionDTO(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    parent_id: uuid.UUID | None = None
    document_count: int = 0
    access: Literal["everyone", "restricted"] = "everyone"
    #: Who a restricted collection is open to; listed for the knowledge base's managers only.
    grants: list[RagLiteCollectionGrantDTO] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RagLiteDocumentMoveRequest(BaseModel):
    collection_id: uuid.UUID | None = None


class RagLiteSummaryResponse(BaseModel):
    summary: str


class RagLiteGraphSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k_collections: int = Field(default=3, ge=1, le=20)
    top_k_chunks: int = Field(default=5, ge=1, le=50)


class RagLiteGraphSearchCollectionHit(BaseModel):
    collection_id: uuid.UUID
    collection_name: str
    score: float


class RagLiteGraphSearchResponse(BaseModel):
    query: str
    collection_hits: list[RagLiteGraphSearchCollectionHit]
    results: list[RagLiteSearchResult]


# -----------------------------------------------------------------------
# Jobs
# -----------------------------------------------------------------------


class RagLiteJobEventDTO(BaseModel):
    id: uuid.UUID
    event_type: str
    message: str
    created_at: datetime


class RagLiteJobDTO(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    error_message: str | None = None
    stats_json: dict | None = None
    created_at: datetime
    updated_at: datetime
    events: list[RagLiteJobEventDTO] = []


# -----------------------------------------------------------------------
# Config / Health
# -----------------------------------------------------------------------


class RagLiteHealthResponse(BaseModel):
    status: str = "ok"
    mode: str = "lite"


class RagLiteConfigDTO(BaseModel):
    embedding_provider_id: str
    embedding_model: str
    embedding_dim: int
    chunk_max_tokens: int
    chunk_overlap_tokens: int
    file_store_root: str
    upload_max_file_mb: int
