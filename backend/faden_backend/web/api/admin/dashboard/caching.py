"""HTTP caching for dashboard responses: none.

They describe the platform to an administrator; a browser cache, nginx or any
proxy in between must not keep a copy for the next person who asks.
"""

from __future__ import annotations

from fastapi import Response

NO_STORE = "no-store, private"


async def no_store(response: Response) -> None:
    """Router dependency: mark every dashboard response as not to be stored.

    Applies to what an endpoint returns as data. An endpoint that builds its
    own ``Response`` sets the header itself.
    """
    response.headers["Cache-Control"] = NO_STORE
