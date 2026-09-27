"""The PII dashboard's totals count each scan once.

They were selected from a subquery of the events table while naming the
table's own columns, and Postgres joined the two: every event with every
event. Three events counted as nine; 24 k events meant 577 M rows and a
dashboard that never loaded.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.pii_event_dao import PIIEventDAO
from llm_port_backend.db.models.pii import PIIScanEvent

pytestmark = pytest.mark.anyio()


async def test_the_totals_count_each_scan_once(dbsession: AsyncSession) -> None:
    dbsession.add_all([
        PIIScanEvent(operation="scan", pii_detected=True, entities_found=2, source="gateway"),
        PIIScanEvent(operation="redact", pii_detected=True, entities_found=3, source="gateway"),
        PIIScanEvent(operation="scan", pii_detected=False, entities_found=0, source="api"),
    ])
    await dbsession.flush()

    stats = await PIIEventDAO(dbsession).get_stats()

    assert (stats["total_scans"], stats["total_with_pii"], stats["total_entities"]) == (3, 2, 5)
    assert stats["detection_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert stats["operation_breakdown"] == {"scan": 2, "redact": 1}
