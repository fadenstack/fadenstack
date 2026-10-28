"""The gateway waits for RabbitMQ at start-up for as long as it takes (services/rabbit/lifespan.py).

After a reboot Docker starts every container at once and RabbitMQ may need minutes; the gateway gave up after
12 tries (a minute), stayed up without workers, unhealthy, and was never restarted.
"""

import pytest

from faden_gateway.services.rabbit.lifespan import BROKER_RETRY_CAP_S, wait_for_broker


@pytest.mark.anyio
async def test_the_gateway_waits_past_the_old_minute_and_starts() -> None:
    """15 failures (more than the 12 it allowed), then RabbitMQ is there: started, pauses capped."""
    tries: list[int] = []
    pauses: list[float] = []

    async def start() -> None:
        tries.append(len(tries) + 1)
        if len(tries) <= 15:
            raise ConnectionRefusedError(111, "Connection refused")

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    await wait_for_broker(start, sleep=sleep)
    assert len(tries) == 16
    assert pauses[:3] == [5.0, 10.0, 15.0]
    assert max(pauses) == BROKER_RETRY_CAP_S
