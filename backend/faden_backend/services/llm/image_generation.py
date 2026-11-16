"""Image generation is off in this version: the console's side of the gateway's switch.

The gateway decides (``faden_gateway.services.gateway.image_generation.ENABLED``): while it is off, no
picture is made and none a model makes leaves it, on every route (EU AI Act Art. 5 from 2 December 2026:
this version has no safeguard against generating harmful images). This module mirrors that switch for what
the backend and the console do about it:

- a new image deployment is refused (:class:`ImagesDisabledError`: 403, ``images_disabled``), from the
  marketplace (before anything is downloaded) and from the deployments API; one that exists is not touched:
  it keeps running until an administrator stops it, and is never asked;
- the console reads ``GET /api/llm/marketplace/image-generation``: with it off, it offers no image hosting
  and shows the note in each of its languages.

There is no setting: a later version flips both switches together, with the safeguard.
``tests/test_image_generation_off.py`` fails when this one and the gateway's differ.
"""

from __future__ import annotations

from typing import Any, Final

#: Mirrors the gateway's switch; never a setting. Tests may patch it.
ENABLED: Final[bool] = False

#: The refusal's stable code, the same as the gateway's.
CODE: Final = "images_disabled"

#: What the refusal says (the owner's text, 16 Nov 2026), as the gateway's; the console translates it.
NOTE: Final = (
    "Image generation is turned off in this version. From 2 December 2026 the EU AI Act (Article 5) requires "
    "safeguards against generating harmful images, and this version does not have them yet. Image generation "
    "will return in a later version, with those safeguards."
)


def refusal_detail() -> dict[str, Any]:
    """The refusal as an API error's ``detail``."""
    return {"code": CODE, "message": NOTE}
