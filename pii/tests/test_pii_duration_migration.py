"""Both a clean upgrade and a canary's existing duration data must survive."""

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa


@pytest.fixture(params=["backend/faden_backend", "pii/faden_pii"])
def migration(request):
    root = Path(__file__).resolve().parents[2]
    path = (
        root / request.param / "db/migrations/versions/2026-10-20-10-00_p11dur4t10n.py"
    )
    spec = importlib.util.spec_from_file_location("duration_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("canary_column", [False, True])
def test_duration_upgrade_accepts_clean_and_canary_schemas(migration, canary_column):
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    columns = [sa.Column("id", sa.Integer, primary_key=True)]
    if canary_column:
        columns.append(sa.Column("duration_ms", sa.Float, nullable=True))
    table = sa.Table("pii_scan_events", metadata, *columns)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            table.insert().values(
                **({"id": 1, "duration_ms": 125.5} if canary_column else {"id": 1})
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(
            sa.text("SELECT duration_ms FROM pii_scan_events WHERE id=1")
        ).scalar() == (125.5 if canary_column else None)
        columns = {
            c["name"]: c for c in sa.inspect(connection).get_columns("pii_scan_events")
        }
        assert isinstance(columns["duration_ms"]["type"], sa.Float)
        assert columns["duration_ms"]["nullable"] is True


def test_duration_upgrade_rejects_incompatible_existing_column(migration):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE pii_scan_events (id INTEGER, duration_ms VARCHAR(30))"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="incompatible schema"):
                migration.upgrade()


def test_duration_offline_upgrade_can_be_reviewed(migration):
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
    assert "ADD COLUMN IF NOT EXISTS duration_ms DOUBLE PRECISION" in output.getvalue()
