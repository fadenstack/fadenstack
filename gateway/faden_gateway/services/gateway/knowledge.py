"""Knowledge the model looks up itself: search and open, as tools.

Retrieval used to run before the model on every request that asked for it:
the last user message searched RAG Lite and the results went in as a system
message, needed or not. Now the model decides. It gets two tools --
``knowledge_search`` to find passages, ``knowledge_open`` to read around one --
and the gateway runs them, as the user who asked: RAG Lite searches what that
person may read, and an agent's searches stay within its collections.

The backend takes console sign-ins only, so the caller's own token -- an API
token, an agent's -- was refused there and the tools failed for every SDK and
add-in caller. The gateway, which has checked the caller, hands the backend a
token of a minute instead (:func:`reader_token`), good for the knowledge
endpoints alone.

They are offered only to a route whose model answers with tool calls
(``node_metadata["tools"]``); vLLM refuses a request that offers tools
otherwise. And only to someone with something to search
(:func:`has_something_to_search`): a person who may read no document, or an
agent whose collections hold none, got the tools anyway, and a model's first
call was a search that failed.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import jwt

from faden_gateway.services.gateway.rag_lite_client import RagLiteClient
from faden_gateway.settings import settings

if TYPE_CHECKING:
    from faden_gateway.services.gateway.auth import AuthContext

logger = logging.getLogger(__name__)

SEARCH = "knowledge_search"
OPEN = "knowledge_open"
NAMES = frozenset({SEARCH, OPEN})

#: The audience the backend's knowledge endpoints take from the gateway (its services/knowledge_access.py).
AUDIENCE = "faden:knowledge"
#: How long a knowledge token lasts: one search or one passage.
TOKEN_TTL_S = 60


def reader_token(auth: AuthContext) -> str | None:
    """A minute's token naming *auth*'s person, and an agent's collections, for the knowledge endpoints.

    Without the shared secret (a gateway that only reads claims) the caller's own token goes instead.
    """
    if not settings.jwt_secret or not auth.user_id:
        return auth.token
    now = int(time.time())
    claims: dict[str, Any] = {"sub": str(auth.user_id), "aud": AUDIENCE, "iat": now, "exp": now + TOKEN_TTL_S}
    if auth.knowledge is not None:
        claims["collections"] = list(auth.knowledge)
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm or "HS256")


#: How long the answer to "has this person anything to search" is kept. The tools are offered on every chat
#: turn, and the answer is a call to the backend: kept, it costs one small call a minute.
SEARCHABLE_TTL_S = 60.0
#: A check that failed is asked again sooner; until then the tools are offered, as before there was a check.
SEARCHABLE_RETRY_S = 15.0
_SEARCHABLE_MAX = 10_000
#: (person, an agent's collections) -> (whether there is anything to search, until when that holds).
_searchable: dict[tuple[str, tuple[str, ...] | None], tuple[bool, float]] = {}

#: A knowledge tool's answer when there is nothing to search: not an error, the model moves on.
NOTHING_TO_SEARCH = json.dumps({"results": [], "note": "There is nothing in the knowledge base to search."})
#: Beside a search's empty results.
NOTHING_FOUND = "Nothing in the knowledge base matched this search."


async def has_something_to_search(
    client: RagLiteClient, auth: AuthContext, *, now: float | None = None,
) -> bool:
    """Whether *auth*'s person -- within an agent's collections -- has anything in the knowledge base.

    Asked of the backend once a minute per person and collections, not on every turn. A check that fails
    answers yes: the tools are offered as before, and a search that then fails tells the model so.
    """
    key = (str(auth.user_id), auth.knowledge)
    moment = time.monotonic() if now is None else now
    kept = _searchable.get(key)
    if kept is not None and kept[1] > moment:
        return kept[0]
    try:
        answer, ttl = await client.searchable(api_token=reader_token(auth)), SEARCHABLE_TTL_S
    except Exception as exc:  # the chat goes on without the answer
        logger.warning("Could not tell whether user %s has anything to search: %r", auth.user_id, exc)
        answer, ttl = True, SEARCHABLE_RETRY_S
    if len(_searchable) >= _SEARCHABLE_MAX and key not in _searchable:
        for stale in [k for k, (_, until) in _searchable.items() if until <= moment]:
            del _searchable[stale]
        if len(_searchable) >= _SEARCHABLE_MAX:  # still full: the answer that ends soonest goes
            del _searchable[min(_searchable, key=lambda k: _searchable[k][1])]
    _searchable[key] = (answer, moment + ttl)
    return answer


def forget_searchable() -> None:
    """Forgets every answer to whether there is anything to search (tests)."""
    _searchable.clear()


#: Passages a search returns at most, whatever the model asks for.
_MAX_RESULTS = 10

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": SEARCH,
            "description": (
                "Search the organisation's knowledge base -- the documents "
                "uploaded to it -- for passages relevant to a question. Use it "
                "when the answer may depend on internal documents, and write "
                "the query as a standalone question. Each result names its "
                "source: cite it when you use the passage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for, as a standalone question."},
                    "top_k": {
                        "type": "integer",
                        "description": f"How many passages to return (1-{_MAX_RESULTS}, default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": OPEN,
            "description": (
                "Read more of a document found by knowledge_search: the passage "
                "around one of its results, to follow it into its section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "The document_id of a search result."},
                    "chunk": {"type": "integer", "description": "The chunk of that result."},
                    "around": {
                        "type": "integer",
                        "description": "How many chunks to read either side (0-5, default 2).",
                    },
                },
                "required": ["document_id", "chunk"],
            },
        },
    },
]


@dataclass(slots=True)
class KnowledgeResult:
    """What a knowledge tool returned, for the model and the audit log."""

    content: str
    is_error: bool
    latency_ms: int


class KnowledgeTools:
    """Runs the knowledge tools for one request, as the user who made it."""

    def __init__(self, client: RagLiteClient, *, token: str | None) -> None:
        self._client = client
        self._token = token

    async def run(self, name: str, arguments: dict[str, Any]) -> KnowledgeResult:
        started = time.perf_counter()
        try:
            if name == SEARCH:
                content = await self._search(arguments)
            elif name == OPEN:
                content = await self._open(arguments)
            else:
                raise ValueError(f"No knowledge tool named {name}.")
            is_error = False
        except Exception as exc:  # the model is told, and can go on
            content, is_error = json.dumps({"error": str(exc)}), True
        return KnowledgeResult(content, is_error, int((time.perf_counter() - started) * 1000))

    async def _search(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("knowledge_search needs a query.")
        top_k = max(1, min(int(arguments.get("top_k") or 5), _MAX_RESULTS))
        results = await self._client.search(query=query, top_k=top_k, api_token=self._token)
        answer: dict[str, Any] = {
            "query": query,
            "results": [
                {
                    "source": r.get("filename", "unknown"),
                    "document_id": str(r.get("document_id", "")),
                    "chunk": r.get("chunk_index"),
                    "score": round(float(r.get("score") or 0), 3),
                    "text": r.get("chunk_text", ""),
                }
                for r in results
            ],
        }
        if not results:  # said in words: the model tells the user, and goes on without it
            answer["note"] = NOTHING_FOUND
        return json.dumps(answer)

    async def _open(self, arguments: dict[str, Any]) -> str:
        # The model chose it, and it goes into a URL path: an id, or nothing.
        try:
            document_id = str(uuid.UUID(str(arguments.get("document_id") or "").strip()))
        except ValueError:
            raise ValueError("knowledge_open needs the document_id of a search result.") from None
        passage = await self._client.passage(
            document_id=document_id,
            chunk=int(arguments.get("chunk") or 0),
            around=int(arguments.get("around") if arguments.get("around") is not None else 2),
            api_token=self._token,
        )
        return json.dumps({
            "source": passage.get("filename", "unknown"),
            "document_id": document_id,
            "chunks_in_document": passage.get("chunk_count"),
            "text": "\n\n".join(c.get("text", "") for c in passage.get("chunks", [])),
            "chunks": [c.get("chunk_index") for c in passage.get("chunks", [])],
        })


def calls_tools(candidate: Any) -> bool:
    """Whether the route's model answers with tool calls.

    Said by the route (``node_metadata["tools"]``). A remote API that says
    nothing is taken to (OpenAI-compatible APIs do); a local vLLM that says
    nothing is not, since vLLM refuses offered tools unless started for them.
    """
    said = (getattr(candidate, "node_metadata", None) or {}).get("tools")
    if said is not None:
        return bool(said)
    return str(getattr(getattr(candidate, "provider_type", None), "value", "")).startswith("remote_")
