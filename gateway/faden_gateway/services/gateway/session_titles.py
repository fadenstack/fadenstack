"""A chat session's title, taken from its first message.

The chat page names a session it creates from the start of the first
message. A session created elsewhere -- the API, the SDK, a script -- had
no title and stayed "Untitled chat". The gateway now names those the same
way when their first user message arrives.

The title is stored encrypted (like the messages); it is shown to its
author, who wrote it.
"""

from __future__ import annotations

import re

TITLE_MAX_CHARS = 80


def title_from_message(text: str) -> str | None:
    """The start of *text* on one line, cut at a word within the limit."""
    one_line = re.sub(r"\s+", " ", text).strip()
    if not one_line:
        return None
    if len(one_line) <= TITLE_MAX_CHARS:
        return one_line
    cut = one_line[: TITLE_MAX_CHARS - 1]
    # Back to the last space, unless that would leave too little.
    space = cut.rfind(" ")
    if space >= TITLE_MAX_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"
