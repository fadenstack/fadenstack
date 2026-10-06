"""DAOs for the RAG Lite subsystem."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends
from sqlalchemy import delete as sa_delete
from sqlalchemy import false, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.rag_lite import (
    RagLiteChunk,
    RagLiteCollection,
    RagLiteCollectionGrant,
    RagLiteDocument,
    RagLiteDocumentStatus,
    RagLiteEventType,
    RagLiteIngestEvent,
    RagLiteIngestJob,
    RagLiteJobStatus,
)


@dataclass(frozen=True)
class CollectionScope:
    """Which chunks a search may see: those of ``collections``, and with ``unfiled`` those in no collection.

    Built from who is searching (services/knowledge_access.py); ``None`` in its place means everything.
    An empty scope matches nothing -- unlike an empty ``collection_ids``, which has always meant "all".
    """

    collections: frozenset[uuid.UUID]
    unfiled: bool = False

    def narrowed(self, collection_ids: list[uuid.UUID] | None) -> CollectionScope:
        """This scope within the collections a search asked for (those alone, nothing unfiled)."""
        if not collection_ids:
            return self
        return CollectionScope(self.collections & frozenset(collection_ids), unfiled=False)

    def where(self) -> tuple[str, dict[str, Any], tuple[str, ...]]:
        """The SQL condition on ``c.collection_id``, its parameters, and a key for cached statistics."""
        cids = sorted(str(c) for c in self.collections)
        parts: list[str] = []
        params: dict[str, Any] = {}
        if cids:
            parts.append("c.collection_id = ANY(CAST(:cids AS uuid[]))")
            params["cids"] = cids
        if self.unfiled:
            parts.append("c.collection_id IS NULL")
        sql = f"AND ({' OR '.join(parts)})" if parts else "AND FALSE"
        return sql, params, (*cids, *(("<unfiled>",) if self.unfiled else ()))

    def documents(self) -> Any:
        """The same, as a condition on documents."""
        parts: list[Any] = []
        if self.collections:
            parts.append(RagLiteDocument.collection_id.in_(self.collections))
        if self.unfiled:
            parts.append(RagLiteDocument.collection_id.is_(None))
        return or_(*parts) if parts else false()


def _scope_of(
    collection_ids: list[uuid.UUID] | None, scope: CollectionScope | None,
) -> CollectionScope | None:
    """The scope a search runs in: what may be seen, within what was asked for."""
    if scope is not None:
        return scope.narrowed(collection_ids)
    if collection_ids:
        return CollectionScope(frozenset(collection_ids))
    return None


# -----------------------------------------------------------------------
# Collection DAO
# -----------------------------------------------------------------------


class RagLiteCollectionDAO:
    """CRUD operations for RAG Lite collections."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        name: str,
        description: str | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> RagLiteCollection:
        col = RagLiteCollection(
            id=uuid.uuid4(),
            name=name,
            description=description,
            parent_id=parent_id,
        )
        self.session.add(col)
        await self.session.flush()
        return col

    async def get(self, collection_id: uuid.UUID) -> RagLiteCollection | None:
        result = await self.session.execute(
            select(RagLiteCollection).where(RagLiteCollection.id == collection_id),
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[RagLiteCollection]:
        result = await self.session.execute(
            select(RagLiteCollection).order_by(RagLiteCollection.name),
        )
        return list(result.scalars().all())

    async def update(
        self,
        collection_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = ...,  # type: ignore[assignment]
        parent_id: uuid.UUID | None = ...,  # type: ignore[assignment]
    ) -> RagLiteCollection | None:
        col = await self.get(collection_id)
        if col is None:
            return None
        if name is not None:
            col.name = name
        if description is not ...:
            col.description = description
        if parent_id is not ...:
            col.parent_id = parent_id
        return col

    async def grants_of(self, collection_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[RagLiteCollectionGrant]]:
        """The grants of each of *collection_ids*."""
        if not collection_ids:
            return {}
        rows = await self.session.execute(
            select(RagLiteCollectionGrant).where(RagLiteCollectionGrant.collection_id.in_(collection_ids)),
        )
        out: dict[uuid.UUID, list[RagLiteCollectionGrant]] = {}
        for grant in rows.scalars():
            out.setdefault(grant.collection_id, []).append(grant)
        return out

    async def set_access(
        self, collection: RagLiteCollection, access: str, grants: list[tuple[str, uuid.UUID]] | None,
    ) -> None:
        """Set *collection*'s access, and with *grants* replace who it is open to."""
        collection.access = access
        if grants is not None:
            await self.session.execute(
                sa_delete(RagLiteCollectionGrant).where(RagLiteCollectionGrant.collection_id == collection.id),
            )
            for principal_type, principal_id in dict.fromkeys(grants):
                self.session.add(
                    RagLiteCollectionGrant(
                        id=uuid.uuid4(),
                        collection_id=collection.id,
                        principal_type=principal_type,
                        principal_id=principal_id,
                    ),
                )
        await self.session.flush()
        # The flush expired what the database sets (updated_at); read it back here, not lazily later.
        await self.session.refresh(collection)

    async def list_with_doc_counts(self) -> list[tuple[RagLiteCollection, int]]:
        """Return all collections with their direct document counts."""
        stmt = (
            select(
                RagLiteCollection,
                func.count(RagLiteDocument.id).label("doc_count"),
            )
            .outerjoin(
                RagLiteDocument,
                RagLiteDocument.collection_id == RagLiteCollection.id,
            )
            .group_by(RagLiteCollection.id)
            .order_by(RagLiteCollection.name)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def delete(self, collection_id: uuid.UUID) -> bool:
        col = await self.get(collection_id)
        if col is None:
            return False
        await self.session.delete(col)
        return True


# -----------------------------------------------------------------------
# Document DAO
# -----------------------------------------------------------------------


class RagLiteDocumentDAO:
    """CRUD operations for RAG Lite documents."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        *,
        filename: str,
        doc_type: str,
        size_bytes: int,
        sha256: str,
        collection_id: uuid.UUID | None = None,
        content_text: str | None = None,
        metadata_json: dict | None = None,
        chunk_count: int = 0,
        status: RagLiteDocumentStatus = RagLiteDocumentStatus.PENDING,
        file_store_key: str | None = None,
    ) -> RagLiteDocument:
        doc = RagLiteDocument(
            id=uuid.uuid4(),
            collection_id=collection_id,
            filename=filename,
            doc_type=doc_type,
            content_text=content_text,
            size_bytes=size_bytes,
            sha256=sha256,
            metadata_json=metadata_json,
            chunk_count=chunk_count,
            status=status,
            file_store_key=file_store_key,
        )
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def get(self, document_id: uuid.UUID) -> RagLiteDocument | None:
        result = await self.session.execute(
            select(RagLiteDocument).where(RagLiteDocument.id == document_id),
        )
        return result.scalar_one_or_none()

    async def list_by_collection(
        self,
        collection_id: uuid.UUID | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
        scope: CollectionScope | None = None,
    ) -> list[RagLiteDocument]:
        query = select(RagLiteDocument).order_by(RagLiteDocument.created_at.desc())
        if collection_id is not None:
            query = query.where(RagLiteDocument.collection_id == collection_id)
        if scope is not None:
            query = query.where(scope.documents())
        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def exists_by_sha256(self, sha256: str) -> bool:
        result = await self.session.execute(
            select(RagLiteDocument.id)
            .where(RagLiteDocument.sha256 == sha256)
            .limit(1),
        )
        return result.scalar_one_or_none() is not None

    async def update_status(
        self,
        document_id: uuid.UUID,
        status: RagLiteDocumentStatus,
        *,
        content_text: str | None = ...,  # type: ignore[assignment]
        metadata_json: dict | None = ...,  # type: ignore[assignment]
        chunk_count: int | None = None,
    ) -> RagLiteDocument | None:
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.status = status
        if content_text is not ...:
            doc.content_text = content_text
        if metadata_json is not ...:
            doc.metadata_json = metadata_json
        if chunk_count is not None:
            doc.chunk_count = chunk_count
        return doc

    async def move_to_collection(
        self,
        document_id: uuid.UUID,
        collection_id: uuid.UUID | None,
    ) -> RagLiteDocument | None:
        """Move a document to a different collection (or root if None)."""
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.collection_id = collection_id
        # Also update the denormalised collection_id on chunks
        await self.session.execute(
            update(RagLiteChunk)
            .where(RagLiteChunk.document_id == document_id)
            .values(collection_id=collection_id),
        )
        return doc

    async def update_summary(
        self,
        document_id: uuid.UUID,
        summary: str | None,
    ) -> RagLiteDocument | None:
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.summary = summary
        return doc

    async def list_all(
        self,
        *,
        limit: int = 500,
        offset: int = 0,
        scope: CollectionScope | None = None,
    ) -> list[RagLiteDocument]:
        """Return all documents regardless of collection (within *scope*)."""
        query = select(RagLiteDocument).order_by(RagLiteDocument.created_at.desc())
        if scope is not None:
            query = query.where(scope.documents())
        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def delete(self, document_id: uuid.UUID) -> bool:
        doc = await self.get(document_id)
        if doc is None:
            return False
        await self.session.delete(doc)
        return True


# -----------------------------------------------------------------------
# Chunk DAO
# -----------------------------------------------------------------------


class RagLiteChunkDAO:
    """Operations for RAG Lite chunks (bulk insert + vector search)."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def bulk_create(self, chunks: list[dict[str, Any]]) -> int:
        """Batch-insert chunks. Each dict must contain the column values.

        Returns the number of chunks inserted.
        """
        if not chunks:
            return 0
        objs = [RagLiteChunk(**c) for c in chunks]
        self.session.add_all(objs)
        await self.session.flush()
        return len(objs)

    async def passage(
        self,
        document_id: uuid.UUID,
        *,
        first: int,
        last: int,
    ) -> list[RagLiteChunk]:
        """The chunks of a document from *first* to *last*, in order."""
        result = await self.session.execute(
            select(RagLiteChunk)
            .where(
                RagLiteChunk.document_id == document_id,
                RagLiteChunk.chunk_index >= first,
                RagLiteChunk.chunk_index <= last,
            )
            .order_by(RagLiteChunk.chunk_index),
        )
        return list(result.scalars().all())

    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        stmt = sa_delete(RagLiteChunk).where(
            RagLiteChunk.document_id == document_id,
        )
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]

    async def search_similar(
        self,
        query_vector: list[float],
        top_k: int = 5,
        collection_ids: list[uuid.UUID] | None = None,
        scope: CollectionScope | None = None,
    ) -> list[dict[str, Any]]:
        """Cosine-similarity search via pgvector ``<=>`` operator.

        Returns a list of dicts with ``chunk_text``, ``chunk_index``,
        ``document_id``, ``filename``, and ``score``.
        """
        # Build parameterised raw SQL — pgvector operators are not yet
        # first-class in the SQLAlchemy ORM layer.
        filters = ""
        params: dict[str, Any] = {
            "query": str(query_vector),
            "top_k": top_k,
        }
        searched = _scope_of(collection_ids, scope)
        if searched is not None:
            filters, where_params, _key = searched.where()
            params.update(where_params)
            # When Postgres uses the HNSW index here, it finds the nearest
            # chunks of all collections and the filter then drops the others'
            # -- with a small collection among large ones, fewer results than
            # asked, or none (pgvector's documented filtering limit; at the
            # sizes measured so far Postgres chose an exact scan instead).
            # pgvector 0.8 keeps scanning until the filter is met; its
            # results can come slightly out of order, hence the outer sort.
            await self.session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))

        sql = text(
            f"""
            SELECT * FROM (
                SELECT c.id,
                       c.chunk_text,
                       c.chunk_index,
                       d.filename,
                       d.id AS document_id,
                       c.embedding <=> CAST(:query AS vector) AS distance
                FROM rag_lite_chunks c
                JOIN rag_lite_documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                  {filters}
                ORDER BY c.embedding <=> CAST(:query AS vector)
                LIMIT :top_k
            ) nearest
            ORDER BY distance
            """,
        )
        result = await self.session.execute(sql, params)
        rows = result.mappings().all()
        return [
            {
                "chunk_id": str(r["id"]),
                "chunk_text": r["chunk_text"],
                "chunk_index": r["chunk_index"],
                "filename": r["filename"],
                "document_id": str(r["document_id"]),
                "score": 1.0 - float(r["distance"]),
            }
            for r in rows
        ]

    async def search_lexical(
        self,
        query: str,
        top_k: int = 5,
        collection_ids: list[uuid.UUID] | None = None,
        scope: CollectionScope | None = None,
    ) -> list[dict[str, Any]]:
        """Keyword search over ``content_tsv``, ranked by BM25.

        The query's words are stemmed like the index (English) and any of
        them may match: a question rarely shares all its words with the
        passage that answers it. Ranked by BM25 (k1 = 1.2, b = 0.75) over the
        searched collections: a rare word counts for more than a common one.
        Postgres's own ``ts_rank_cd`` has no such weighting; fused with vector
        search it pulled good results down (RAGBench hit@1 0.92 -> 0.72 on
        hotpotqa).

        Scoring every chunk that shares a word with the query does not scale:
        on 200,000 chunks a 13-word question matched 85,625 of them and took
        1.9 s. So, as search engines do (Lucene's MaxScore):

        - The candidates are the chunks with the query's *rarest* words, as
          many words as fit a budget of matches. A chunk sharing only common
          words with the query cannot reach the top -- common words weigh
          next to nothing -- and it is not scored. Every candidate is scored
          on all of the query's words.
        - A collection's size and average chunk length, and each word's
          document count, are kept a minute per process (``_Bm25Stats``):
          they change only with ingestion, and BM25 barely moves with them.
        - A word's document count is counted up to a cap; one past it is
          common, and its (small) weight comes from Postgres's own column
          statistics.

        A chunk is scored on the query's words alone: ``setweight`` marks them
        in its ``tsvector`` and ``ts_filter`` keeps the marked ones, so it is
        taken apart into a few words, not all of its hundred.
        """
        filters = ""
        where: dict[str, Any] = {}
        key: tuple[str, ...] = ("*",)
        searched = _scope_of(collection_ids, scope)
        if searched is not None:
            filters, where, key = searched.where()

        lexemes: list[str] = (await self.session.execute(
            text("SELECT tsvector_to_array(to_tsvector('english', :query))"), {"query": query},
        )).scalar_one() or []
        if not lexemes:
            return []
        n, avgdl = await self._collection_stats(key, filters, where)
        if not n:
            return []
        df = await self._document_counts(key, lexemes, n, filters, where)
        known = sorted((t for t in lexemes if df[t] > 0), key=lambda t: df[t])
        candidates: list[str] = []
        matched = 0.0
        for term in known:
            if _is_common(df[term], n) or (candidates and matched + df[term] > _CANDIDATE_BUDGET):
                break
            candidates.append(term)
            matched += df[term]
        if not candidates:
            return []

        sql = text(
            f"""
            WITH df AS (
                SELECT * FROM unnest(CAST(:lexemes AS text[]), CAST(:dfs AS float8[])) AS t(lexeme, df)
            ),
            q AS MATERIALIZED (
                SELECT to_tsquery('simple', string_agg(quote_literal(term), ' | ')) AS query
                FROM unnest(CAST(:candidates AS text[])) AS term
            ),
            matches AS MATERIALIZED (
                SELECT c.id, ts_filter(setweight(c.content_tsv, 'A', CAST(:lexemes AS text[])), '{{a}}') AS hits,
                       c.content_len::float8 AS dl
                FROM rag_lite_chunks c, q
                WHERE c.content_tsv @@ q.query {filters}
            ),
            scored AS (
                SELECT m.id,
                       sum(
                           ln(1 + (:n - df.df + 0.5) / (df.df + 0.5))
                           * (array_length(u.positions, 1) * 2.2)
                           / (array_length(u.positions, 1) + 1.2 * (0.25 + 0.75 * m.dl / :avgdl))
                       ) AS score
                FROM matches m
                CROSS JOIN LATERAL unnest(m.hits) AS u(lexeme, positions, weights)
                JOIN df ON df.lexeme = u.lexeme
                GROUP BY m.id
                ORDER BY score DESC
                LIMIT :top_k
            )
            SELECT c.id, c.chunk_text, c.chunk_index, d.filename, d.id AS document_id, s.score
            FROM scored s
            JOIN rag_lite_chunks c ON c.id = s.id
            JOIN rag_lite_documents d ON d.id = c.document_id
            ORDER BY s.score DESC
            """,
        )
        params = {
            **where, "lexemes": known, "dfs": [df[t] for t in known], "candidates": candidates,
            "n": float(n), "avgdl": avgdl, "top_k": top_k,
        }
        rows = (await self.session.execute(sql, params)).mappings().all()
        return [
            {
                "chunk_id": str(r["id"]),
                "chunk_text": r["chunk_text"],
                "chunk_index": r["chunk_index"],
                "filename": r["filename"],
                "document_id": str(r["document_id"]),
                "score": float(r["score"]),
            }
            for r in rows
        ]

    async def _collection_stats(
        self, key: tuple[str, ...], filters: str, scope: dict[str, Any],
    ) -> tuple[int, float]:
        """How many chunks the searched collections hold, and their average length."""
        cached = _BM25_STATS.stats(key)
        if cached is not None:
            return cached
        row = (await self.session.execute(
            text(
                "SELECT count(*), coalesce(avg(c.content_len), 1) FROM rag_lite_chunks c "
                f"WHERE c.content_len IS NOT NULL {filters}",
            ),
            scope,
        )).one()
        stats = (int(row[0]), max(float(row[1]), 1.0))
        _BM25_STATS.keep_stats(key, stats)
        return stats

    async def _document_counts(
        self, key: tuple[str, ...], lexemes: list[str], n: int, filters: str, scope: dict[str, Any],
    ) -> dict[str, float]:
        """How many chunks of the searched collections hold each word.

        Counted up to ``_DF_CAP``: a word past it is common, and its count is
        estimated from the column statistics Postgres keeps (the share of
        chunks that hold each of the thousand most common words), or counted
        in full when it is not among them.
        """
        counts = {t: _BM25_STATS.count(key, t) for t in lexemes}
        missing = [t for t, v in counts.items() if v is None]
        if missing:
            rows = (await self.session.execute(
                text(
                    f"""
                    SELECT t.lexeme,
                           (SELECT count(*) FROM (
                                SELECT 1 FROM rag_lite_chunks c
                                WHERE c.content_tsv @@ to_tsquery('simple', quote_literal(t.lexeme)) {filters}
                                LIMIT :cap
                           ) hit) AS df
                    FROM unnest(CAST(:lexemes AS text[])) AS t(lexeme)
                    """,
                ),
                {**scope, "lexemes": missing, "cap": _DF_CAP},
            )).all()
            found = {r[0]: float(r[1]) for r in rows}
            capped = [t for t, v in found.items() if v >= _DF_CAP]
            if capped:
                shares = dict((await self.session.execute(
                    text(
                        """
                        SELECT e.lexeme, e.share FROM pg_stats s
                        CROSS JOIN LATERAL unnest(s.most_common_elems::text::text[], s.most_common_elem_freqs)
                            AS e(lexeme, share)
                        WHERE s.tablename = 'rag_lite_chunks' AND s.attname = 'content_tsv'
                          AND e.lexeme = ANY(CAST(:capped AS text[]))
                        """,
                    ),
                    {"capped": capped},
                )).all())
                for term in capped:
                    if term in shares:
                        found[term] = max(float(_DF_CAP), float(shares[term]) * n)
                    else:
                        found[term] = float((await self.session.execute(
                            text(
                                "SELECT count(*) FROM rag_lite_chunks c "
                                f"WHERE c.content_tsv @@ to_tsquery('simple', quote_literal(:term)) {filters}",
                            ),
                            {**scope, "term": term},
                        )).scalar_one())
            for term, value in found.items():
                counts[term] = value
                if value > 0:  # a word no chunk has yet may arrive with the next document
                    _BM25_STATS.keep_count(key, term, value)
        return {t: float(v or 0.0) for t, v in counts.items()}


#: How many matches the candidate words may have between them (see
#: ``search_lexical``). Past it, only rarer words find candidates.
_CANDIDATE_BUDGET = 4000
#: How far a word's document count is counted.
_DF_CAP = 2000


def _is_common(df: float, n: int) -> bool:
    """A word in more than half the chunks: it finds no candidates on its own."""
    return df > n * 0.5


class _Bm25Stats:
    """What BM25 needs besides the chunks, kept a minute per process.

    The collections' size and average chunk length, and each word's document
    count, change only as documents are ingested, and BM25 barely moves with
    them; counting them was most of a search's time on a large collection.
    A word no chunk holds is not kept: the next document may bring it.
    """

    TTL = 60.0
    MAX_COUNTS = 50_000

    def __init__(self) -> None:
        self._stats: dict[tuple[str, ...], tuple[float, tuple[int, float]]] = {}
        self._counts: dict[tuple[tuple[str, ...], str], tuple[float, float]] = {}

    def stats(self, key: tuple[str, ...]) -> tuple[int, float] | None:
        hit = self._stats.get(key)
        return hit[1] if hit and hit[0] > time.monotonic() else None

    def keep_stats(self, key: tuple[str, ...], stats: tuple[int, float]) -> None:
        self._stats[key] = (time.monotonic() + self.TTL, stats)

    def count(self, key: tuple[str, ...], term: str) -> float | None:
        hit = self._counts.get((key, term))
        return hit[1] if hit and hit[0] > time.monotonic() else None

    def keep_count(self, key: tuple[str, ...], term: str, value: float) -> None:
        if len(self._counts) >= self.MAX_COUNTS:
            now = time.monotonic()
            self._counts = {k: v for k, v in self._counts.items() if v[0] > now}
            if len(self._counts) >= self.MAX_COUNTS:
                self._counts.clear()
        self._counts[(key, term)] = (time.monotonic() + self.TTL, value)

    def clear(self) -> None:
        self._stats.clear()
        self._counts.clear()


_BM25_STATS = _Bm25Stats()


# -----------------------------------------------------------------------
# Ingest Job DAO
# -----------------------------------------------------------------------


class RagLiteIngestJobDAO:
    """CRUD operations for RAG Lite ingest jobs."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        document_id: uuid.UUID,
    ) -> RagLiteIngestJob:
        job = RagLiteIngestJob(
            id=uuid.uuid4(),
            document_id=document_id,
            status=RagLiteJobStatus.QUEUED,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> RagLiteIngestJob | None:
        # With its events: they load lazily otherwise, which an async session cannot -- the job's page
        # answered 500 for every job.
        result = await self.session.execute(
            select(RagLiteIngestJob)
            .where(RagLiteIngestJob.id == job_id)
            .options(selectinload(RagLiteIngestJob.events)),
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        job_id: uuid.UUID,
        status: RagLiteJobStatus,
        *,
        error_message: str | None = None,
        stats_json: dict | None = None,
    ) -> RagLiteIngestJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = status
        if error_message is not None:
            job.error_message = error_message
        if stats_json is not None:
            job.stats_json = stats_json
        return job

    async def list_by_document(
        self,
        document_id: uuid.UUID,
    ) -> list[RagLiteIngestJob]:
        result = await self.session.execute(
            select(RagLiteIngestJob)
            .where(RagLiteIngestJob.document_id == document_id)
            .order_by(RagLiteIngestJob.created_at.desc()),
        )
        return list(result.scalars().all())

    async def list_recent(
        self,
        *,
        limit: int = 50,
        status_filter: RagLiteJobStatus | None = None,
    ) -> list[RagLiteIngestJob]:
        query = select(RagLiteIngestJob).options(selectinload(RagLiteIngestJob.events)).order_by(
            RagLiteIngestJob.created_at.desc(),
        )
        if status_filter:
            query = query.where(RagLiteIngestJob.status == status_filter)
        query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def add_event(
        self,
        job_id: uuid.UUID,
        event_type: RagLiteEventType,
        message: str,
    ) -> RagLiteIngestEvent:
        event = RagLiteIngestEvent(
            id=uuid.uuid4(),
            job_id=job_id,
            event_type=event_type,
            message=message,
        )
        self.session.add(event)
        await self.session.flush()
        return event
