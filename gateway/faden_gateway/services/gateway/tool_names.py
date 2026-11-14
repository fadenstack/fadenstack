"""The names the model gets for the gateway's MCP tools, and the names it calls them by.

In the gateway an MCP tool is named ``mcp.<prefix>.<tool>``: rules (``mcp.github.*``), tool classes,
grants, the audit, the call log, the events and a chat's history all use that name. Some providers
refuse it: OpenAI and Azure OpenAI take a tool name of ``[a-zA-Z0-9_-]``, up to 64 characters, and so
does Bedrock's Converse API. So the model gets each MCP tool under a name of those characters --
``mcp_github_search_repositories`` -- on every route, since a chat's history may go to another route
later. One table per request holds both directions:

- **Out**, where a payload leaves for the model (:meth:`ToolNames.out`): the tools, ``tool_choice``,
  the history's calls and the tools' answers, which name tools to call ("There is no tool named ...",
  what a search switched on). The gateway's own payload is not changed.
- **Back**, once, where a round's calls are put together (:meth:`ToolNames.calls_back`): before any
  rule, class, grant or approval reads them. A call by the gateway's name is taken as it is.

How a name is made:

- every character outside ``[A-Za-z0-9_-]`` becomes ``_``;
- a name longer than 64, or one that another tool of the request would also get -- another MCP tool,
  a tool of the client's or of the gateway's own -- is cut and ends in ``_`` and six hex characters of
  the SHA-256 of its full name (of the name and a count, when even those clash);
- a name in the table keeps its form for the rest of the request, so one round's history matches the
  next round's tools; the names are added sorted, so the same names make the same table.

A client's own tools keep the names the client gave them.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

#: The longest tool name OpenAI, Azure OpenAI and Bedrock Converse take.
MAX_CHARS = 64
#: How long the hash that tells two names apart is.
HASH_CHARS = 6
#: The MCP tools' names in the gateway (``mcp_tool_cache.MCP_TOOL_PREFIX``).
_PREFIX = "mcp."
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
#: A called name, without what some models append to it ("<|channel|>commentary").
_CALLED = re.compile(r"^[A-Za-z0-9_-]+")
#: Where a name in a text starts and ends: not inside a longer name.
_BEFORE = r"(?<![A-Za-z0-9_.-])"
_AFTER = r"(?!\.?[A-Za-z0-9_-])"


def _plain(name: str) -> str:
    return _UNSAFE.sub("_", name)


def _hashed(name: str, count: int) -> str:
    digest = hashlib.sha256((name if not count else f"{name}#{count}").encode()).hexdigest()
    return f"{_plain(name)[: MAX_CHARS - HASH_CHARS - 1]}_{digest[:HASH_CHARS]}"


def _loose(name: str) -> str:
    """A name without "-", "_" and case, as a model may vary it."""
    return re.sub(r"[-_]", "", name).lower()


class ToolNames:
    """One request's table: the gateway's names of its MCP tools, and the names the model gets."""

    def __init__(self, taken: Iterable[str] = ()) -> None:
        #: Names the model gets as they are, the client's tools and the gateway's own: no MCP tool's.
        self._taken = frozenset(taken)
        self._taken_loose = frozenset(_loose(name) for name in self._taken)
        self._out: dict[str, str] = {}
        self._back: dict[str, str] = {}
        #: A model's name without "-", "_" and case: the gateway's, or ``None`` when two share it.
        self._loose: dict[str, str | None] = {}
        self._text: re.Pattern[str] | None = None

    def mapped(self, name: str) -> bool:
        """Whether the model gets *name* under another name: an MCP tool's, which no client tool has."""
        return name.startswith(_PREFIX) and name not in self._taken

    def add(self, names: Iterable[str]) -> None:
        """The model's names for the MCP tools among *names*; a name in the table keeps the one it has."""
        fresh = sorted({name for name in names if self.mapped(name) and name not in self._out})
        if not fresh:
            return
        plain = {name: _plain(name) for name in fresh}
        shared = Counter(plain.values())
        clashing = []
        for name in fresh:
            wire = plain[name]
            if len(wire) > MAX_CHARS or shared[wire] > 1 or self._clashes(wire):
                clashing.append(name)
            else:
                self._keep(name, wire)
        for name in clashing:
            count = 0
            while self._clashes(wire := _hashed(name, count)):
                count += 1
            self._keep(name, wire)
        self._text = None

    def wire(self, name: str) -> str:
        """The name the model gets for *name*."""
        if self.mapped(name):
            self.add([name])
            return self._out[name]
        return name

    def back(self, called: str) -> str | None:
        """The gateway's name of the MCP tool the model called *called*; ``None`` when it named no other."""
        match = _CALLED.match(called)
        if match is None:
            return None
        if match.group(0) in self._back:  # a client's tool never has one of these names
            return self._back[match.group(0)]
        loose = _loose(match.group(0))
        return None if loose in self._taken_loose else self._loose.get(loose)

    def calls_back(self, calls: Iterable[dict[str, Any]]) -> None:
        """*calls*, as a round put them together, under the gateway's names: changed in place."""
        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                name = self.back(function["name"])
                if name is not None:
                    function["name"] = name

    def answer_back(self, payload: Any) -> None:
        """The calls of a whole answer (``choices[].message.tool_calls``) under the gateway's names."""
        for choice in (payload.get("choices") or []) if isinstance(payload, dict) else []:
            message = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
                self.calls_back(message["tool_calls"])

    def out(self, payload: dict[str, Any]) -> dict[str, Any]:
        """*payload* as the model gets it, its MCP tools under the model's names; *payload* stays."""
        self.add(_names_in(payload))
        if not self._out:
            return payload
        out = dict(payload)
        if isinstance(payload.get("tools"), list):
            out["tools"] = [self._named(tool) for tool in payload["tools"]]
        if isinstance(payload.get("tool_choice"), dict):
            out["tool_choice"] = self._named(payload["tool_choice"])
        if isinstance(payload.get("messages"), list):
            out["messages"] = [self._message(message) for message in payload["messages"]]
        return out

    def text(self, text: str) -> str:
        """*text* with each MCP tool name of the table as the model calls it."""
        if not self._out:
            return text
        if self._text is None:
            # The longest first: "mcp.a.b x" before "mcp.a.b", which a space would end.
            names = sorted(self._out, key=len, reverse=True)
            self._text = re.compile(f"{_BEFORE}(?:{'|'.join(map(re.escape, names))}){_AFTER}")
        return self._text.sub(lambda match: self._out[match.group(0)], text)

    def _clashes(self, wire: str) -> bool:
        return wire in self._taken or wire in self._back

    def _keep(self, name: str, wire: str) -> None:
        self._out[name] = wire
        self._back[wire] = name
        loose = _loose(wire)
        self._loose[loose] = name if loose not in self._loose else None

    def _named(self, item: Any) -> Any:
        """A tool, a choice or a call (``{"function": {"name": ...}}``) under the model's name."""
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            return item
        if not self.mapped(function["name"]):
            return item
        return {**item, "function": {**function, "name": self.wire(function["name"])}}

    def _message(self, message: Any) -> Any:
        if not isinstance(message, dict):
            return message
        out = dict(message)
        if isinstance(message.get("tool_calls"), list):
            out["tool_calls"] = [self._named(call) for call in message["tool_calls"]]
        if isinstance(message.get("function_call"), dict):
            out["function_call"] = self._named({"function": message["function_call"]})["function"]
        if message.get("role") in ("tool", "function"):
            if isinstance(message.get("name"), str):
                out["name"] = self.wire(message["name"])
            content = message.get("content")
            if isinstance(content, str):
                out["content"] = self.text(content)
            elif isinstance(content, list):
                out["content"] = [
                    {**part, "text": self.text(part["text"])}
                    if isinstance(part, dict) and isinstance(part.get("text"), str) else part
                    for part in content
                ]
        return out


def _listed(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _names_in(payload: dict[str, Any]) -> list[str]:
    """The tool names *payload* holds: its tools, its choice, its history's calls and answers."""
    names: list[str] = []

    def named(item: Any) -> None:
        function = item.get("function") if isinstance(item, dict) else None
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.append(function["name"])

    for tool in _listed(payload.get("tools")):
        named(tool)
    named(payload.get("tool_choice"))
    for message in _listed(payload.get("messages")):
        if not isinstance(message, dict):
            continue
        for call in _listed(message.get("tool_calls")):
            named(call)
        named({"function": message.get("function_call")})
        if message.get("role") in ("tool", "function") and isinstance(message.get("name"), str):
            names.append(message["name"])
    return names
