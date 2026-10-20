"""Controlled failures: an incomplete scan is never a clean result."""


class PIIError(Exception):
    """An actionable service failure without any raw input in its message."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
