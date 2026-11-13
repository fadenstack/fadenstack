"""What the skills service can refuse, as the API turns it into a status."""

from __future__ import annotations


class SkillError(Exception):
    """Base class; ``status_code`` is what the API answers with.

    ``code`` names the refusal for a caller that tells refusals apart (the
    console's texts); the API then answers ``{"detail": {"code", "message"}}``,
    as the backend's own refusals do.
    """

    status_code = 400

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        #: More of the refusal for the caller, next to ``code`` and ``message`` (only with a ``code``).
        self.extra: dict[str, object] = {}


class NotFoundError(SkillError):
    """No such skill -- or one the caller cannot see, which looks the same."""

    status_code = 404


class ForbiddenError(SkillError):
    """The caller can see the skill but may not do this to it."""

    status_code = 403


class ConflictError(SkillError):
    """The request clashes with the skill's state: a taken name, a stale edit."""

    status_code = 409


class LimitError(SkillError):
    """A configured limit (skills per person, published skills) is reached."""

    status_code = 409


class UnknownAgentError(SkillError):
    """Agents a skill does not have yet that the backend did not vouch for (``unknown_agent``, with their ``ids``).

    The same refusal the backend gives when it can tell before asking; here it is decided under the tenant's
    assignment lock, so an agent removed a moment before counts as new.
    """

    status_code = 422

    def __init__(self, ids: list[str]) -> None:
        super().__init__("No such agent: " + ", ".join(ids), code="unknown_agent")
        self.extra = {"ids": ids}
