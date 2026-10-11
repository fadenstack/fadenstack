import os
import shutil
from pathlib import Path

import uvicorn

from faden_skills.settings import settings


def set_multiproc_dir() -> None:
    """Set up prometheus multiprocess directory."""
    shutil.rmtree(settings.prometheus_dir, ignore_errors=True)
    Path(settings.prometheus_dir).mkdir(parents=True)
    os.environ["prometheus_multiproc_dir"] = str(
        settings.prometheus_dir.expanduser().absolute(),
    )
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(
        settings.prometheus_dir.expanduser().absolute(),
    )


def main() -> None:
    """Entrypoint of the application."""
    # Outside development, not on a secret published with the source code.
    from faden_skills.published_secrets import refuse_published_secrets  # noqa: PLC0415

    refuse_published_secrets(settings)
    set_multiproc_dir()
    uvicorn.run(
        "faden_skills.web.application:get_app",
        workers=settings.workers_count,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.value.lower(),
        factory=True,
    )


if __name__ == "__main__":
    main()
