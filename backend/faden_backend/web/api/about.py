"""GET /api/about: what the About dialog shows, for anyone signed in.

Not for anonymous callers: a version on a page anyone can open helps someone looking for an unpatched server, so
the sign-in page shows none.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from faden_backend.db.models.users import User, current_active_user
from faden_backend.settings import settings

router = APIRouter()


class AboutDTO(BaseModel):
    """The product, its edition and release, its licence, and whether it links back to fadenstack.com."""

    product: str
    edition: str
    #: The release `faden` installed (VERSION); empty for an install built from a source checkout.
    version: str
    licence: str
    vendor_links: bool


@router.get("/about", response_model=AboutDTO, name="about")
async def about(_user: User = Depends(current_active_user)) -> AboutDTO:
    """The About dialog's facts."""
    edition = (settings.edition or "core").strip().lower() or "core"
    return AboutDTO(
        product="Fadenstack",
        edition=edition,
        version=(settings.install_release or "").strip(),
        licence="Apache-2.0" if edition == "core" else "",
        # Only an edition other than core may switch them off.
        vendor_links=settings.vendor_links or edition == "core",
    )
