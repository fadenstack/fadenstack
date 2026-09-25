"""What the skills service can refuse, as the API turns it into a status."""

from __future__ import annotations


class SkillError(Exception):
    """Base class; ``status_code`` is what the API answers with."""

    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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
