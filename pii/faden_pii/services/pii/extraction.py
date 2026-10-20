"""Extract and rebuild text once, preserving structured argument types."""

from copy import deepcopy
from dataclasses import dataclass
import json
import re
import unicodedata
from typing import Any

from faden_pii.services.pii.errors import PIIError


@dataclass
class TextUnit:
    """A mutable text leaf in a private copy of the request."""

    path: str
    parent: Any
    key: Any
    person_hint: bool = False
    entity_hint: str | None = None

    @property
    def text(self) -> str:
        return self.parent[self.key]


def declares_person(key: Any) -> bool:
    """Use explicit personal-name fields, without guessing what generic 'name' means."""
    if not isinstance(key, str):
        return False
    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", key.casefold()) if c.isalnum()
    )
    return normalized in {
        "firstname",
        "givenname",
        "forename",
        "lastname",
        "surname",
        "fullname",
        "personname",
        "vorname",
        "nachname",
        "familienname",
        "personenname",
        "vollstaendigername",
        "prenom",
        "nomdefamille",
        "nomcomplet",
        "nombredepila",
        "nombrecompleto",
        "apellido",
        "apellidos",
        "apellidopaterno",
        "apellidomaterno",
    }


def name_value(text: str) -> bool:
    """A small target-only value; never infer PII spans in the context itself."""
    return (
        0 < len(text) <= 128
        and 1 <= len(text.split()) <= 8
        and any(c.isalpha() for c in text)
        and all(
            c.isalpha()
            or unicodedata.category(c).startswith("M")
            or c.isspace()
            or c in "'-’‐‑."
            for c in text
        )
    )


def name_question(message: Any) -> bool:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    content = message.get("content")
    return (
        isinstance(content, str)
        and "?" in content[-512:]
        and re.search(
            r"\b(?:first name|given name|full name|last name|Vorname|Nachname|Familienname|prénom|nom de famille|tu nombre|su nombre|apellido)\b",
            content[-512:],
            re.IGNORECASE,
        )
        is not None
    )


def string_leaves(
    value: Any,
    path: str = "",
    depth: int = 0,
    budget: list[int] | None = None,
    declared_entities: set[str] | None = None,
) -> list[TextUnit]:
    """Return string values, never JSON keys or numeric/boolean values."""
    if depth > 64:
        raise PIIError("pii_input_too_deep", 413)
    budget = [0, 0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 4096:
        raise PIIError("pii_input_too_large", 413)
    units = []
    if isinstance(value, dict):
        for key, child in value.items():
            entity = None
            if declared_entities:
                from faden_pii.services.pii.extended import declared_entity

                hint = declared_entity(key)
                entity = hint if hint in declared_entities else None
            if entity and child is not None and not isinstance(child, str):
                # Changing a tool's scalar type can invalidate its schema. Refuse
                # rather than forwarding an unmasked sensitive numeric/container value.
                raise PIIError("pii_unsupported_sensitive_value", 422)
            if isinstance(child, str):
                budget[1] += 1
                if budget[1] > 256:
                    raise PIIError("pii_input_too_large", 413)
                units.append(
                    TextUnit(f"{path}/{key}", value, key, declares_person(key), entity)
                )
            elif isinstance(child, (dict, list)):
                units.extend(
                    string_leaves(
                        child, f"{path}/{key}", depth + 1, budget, declared_entities
                    )
                )
    elif isinstance(value, list):
        for key, child in enumerate(value):
            if isinstance(child, str):
                budget[1] += 1
                if budget[1] > 256:
                    raise PIIError("pii_input_too_large", 413)
                units.append(TextUnit(f"{path}/{key}", value, key))
            elif isinstance(child, (dict, list)):
                units.extend(
                    string_leaves(
                        child, f"{path}/{key}", depth + 1, budget, declared_entities
                    )
                )
    return units


class ExtractedPayload:
    """A copy, ordered text leaves and deferred JSON serialization."""

    def __init__(
        self, payload: dict[str, Any], *, declared_entities: set[str] | None = None
    ) -> None:
        # Check nesting before deepcopy can recurse into an untrusted shape.
        stack = [(payload, 0)]
        containers = 0
        while stack:
            value, depth = stack.pop()
            containers += 1
            if containers > 4096:
                raise PIIError("pii_input_too_large", 413)
            if depth > 64:
                raise PIIError("pii_input_too_deep", 413)
            if isinstance(value, dict):
                stack.extend(
                    (v, depth + 1)
                    for v in value.values()
                    if isinstance(v, (dict, list))
                )
            elif isinstance(value, list):
                stack.extend(
                    (v, depth + 1) for v in value if isinstance(v, (dict, list))
                )
        self.payload = deepcopy(payload)
        self.declared_entities = declared_entities
        self.units: list[TextUnit] = []
        self.arguments: list[tuple[dict, str, Any]] = []
        if any(key in self.payload for key in ("prompt", "query", "documents")):
            # The gateway normalizes image prompts and reranking to input.
            raise PIIError("pii_unsupported_surface")
        messages = self.payload.get("messages", [])
        if not isinstance(messages, list):
            raise PIIError("pii_unsupported_surface")
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise PIIError("pii_unsupported_surface")
            path = f"/messages/{index}"
            content = message.get("content")
            if isinstance(content, str):
                hint = (
                    message.get("role") == "user"
                    and index > 0
                    and name_question(messages[index - 1])
                )
                self.units.append(TextUnit(path + "/content", message, "content", hint))
            elif isinstance(content, list):
                for number, part in enumerate(content):
                    if (
                        not isinstance(part, dict)
                        or part.get("type") != "text"
                        or not isinstance(part.get("text"), str)
                    ):
                        raise PIIError("pii_unsupported_surface")
                    self.units.append(
                        TextUnit(f"{path}/content/{number}/text", part, "text")
                    )
            elif content is not None:
                raise PIIError("pii_unsupported_surface")
            calls = message.get("tool_calls", [])
            if not isinstance(calls, list):
                raise PIIError("pii_unsupported_surface")
            for number, call in enumerate(calls):
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict) or not isinstance(
                    function.get("arguments"), str
                ):
                    raise PIIError("pii_unsupported_surface")
                self._arguments(
                    function,
                    "arguments",
                    f"{path}/tool_calls/{number}/function/arguments",
                )
            if "function_call" in message:
                function = message["function_call"]
                if not isinstance(function, dict) or not isinstance(
                    function.get("arguments"), str
                ):
                    raise PIIError("pii_unsupported_surface")
                self._arguments(
                    function, "arguments", path + "/function_call/arguments"
                )
            if len(self.units) > 256:
                raise PIIError("pii_input_too_large", 413)
        if "input" in self.payload:
            value = self.payload["input"]
            if isinstance(value, str):
                self.units.append(TextUnit("/input", self.payload, "input"))
            elif isinstance(value, list) and len(value) > 256:
                raise PIIError("pii_input_too_large", 413)
            elif isinstance(value, list) and all(isinstance(t, str) for t in value):
                self.units.extend(
                    TextUnit(f"/input/{i}", value, i) for i in range(len(value))
                )
            else:
                raise PIIError("pii_unsupported_surface")

    def _arguments(self, parent: dict, key: str, path: str) -> None:
        try:
            value = json.loads(parent[key])
        except (ValueError, RecursionError) as error:
            raise PIIError("pii_invalid_tool_arguments") from error
        if not isinstance(value, (dict, list)):
            raise PIIError("pii_invalid_tool_arguments")
        units = string_leaves(value, path, declared_entities=self.declared_entities)
        if len(self.units) + len(units) > 256:
            raise PIIError("pii_input_too_large", 413)
        self.units.extend(units)
        self.arguments.append((parent, key, value))

    def rebuild(self) -> dict[str, Any]:
        """Encode only arguments that changed, retaining clean lexical formatting."""
        for parent, key, value in self.arguments:
            if json.loads(parent[key]) != value:
                parent[key] = json.dumps(
                    value, ensure_ascii=False, separators=(",", ":")
                )
        return self.payload
