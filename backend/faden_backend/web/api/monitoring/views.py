from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    """
    Checks the health of a project.

    It returns 200 if the project is healthy. Anyone may ask, so it says nothing else about the install: no
    environment name (sdk/51 F01; ASVS 5.0 V13).
    """
    return {"status": "ok"}
