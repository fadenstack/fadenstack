"""Bounded best-effort metadata delivery, outside inference deadlines."""

import asyncio
import json


class EventDispatcher:
    def __init__(self, send, settings):
        self.send, self.settings = send, settings
        self.queue = asyncio.Queue(maxsize=settings.pii_event_entries)
        self.bytes = self.dropped = self.failed = 0
        self.task = asyncio.create_task(self._run())

    def submit(self, **event):
        if event.get("source") not in ("api", "gateway", "mcp-proxy"):
            event["source"] = "api"
        if event.get("request_id"):
            event["request_id"] = event["request_id"][:128]
        size = len(json.dumps(event).encode())
        if self.queue.full() or self.bytes + size > self.settings.pii_event_bytes:
            self.dropped += 1
            return
        self.bytes += size
        self.queue.put_nowait((event, size))

    async def _run(self):
        while True:
            event, size = await self.queue.get()
            try:
                async with asyncio.timeout(2):
                    if await self.send(**event) is False:
                        self.failed += 1
            except Exception:
                self.failed += 1
            finally:
                self.bytes -= size
                self.queue.task_done()

    async def close(self):
        try:
            async with asyncio.timeout(2):
                await self.queue.join()
        except TimeoutError:
            pass
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        while not self.queue.empty():
            _, size = self.queue.get_nowait()
            self.bytes -= size
            self.dropped += 1
            self.queue.task_done()
