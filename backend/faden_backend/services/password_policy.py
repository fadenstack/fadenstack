"""The one rule a new password must meet, wherever it is set.

Bootstrap (the first administrator), an administrator creating a user, a user
changing their own password, and a reset all check it: ``UserManager``'s
``validate_password`` for the routes fastapi-users serves, :func:`password_problem`
for the others. Passwords set before stay valid; the rule applies when one is set.

Length, not composition: at least :data:`MIN_LENGTH` characters (NIST SP 800-63B's
minimum for a password a person chooses), and not the account's own e-mail address.
"""

from __future__ import annotations

#: Fewest characters a new password may have.
MIN_LENGTH = 8
#: Most characters a new password may have: hashing one is the costly part of a request.
MAX_LENGTH = 128


def password_problem(password: str, email: str | None = None) -> str | None:
    """Why *password* may not be set (for the account *email*), or ``None`` when it may."""
    if len(password) < MIN_LENGTH:
        return f"The password must be at least {MIN_LENGTH} characters long."
    if len(password) > MAX_LENGTH:
        return f"The password must be at most {MAX_LENGTH} characters long."
    if email and password.strip().casefold() == email.strip().casefold():
        return "The password must not be the account's e-mail address."
    return None
