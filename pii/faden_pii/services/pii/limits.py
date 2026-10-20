"""Input and ingress accounting before unbounded JSON/model work."""

import asyncio
import json
import math
import re

from faden_pii.services.pii.errors import PIIError


def validate_units(texts, mapping, settings):
    sizes = [len(t.encode("utf-8")) for t in texts]
    if (
        len(texts) > settings.pii_max_units
        or max(sizes, default=0) > settings.pii_max_unit_bytes
        or sum(sizes) > settings.pii_extracted_bytes
    ):
        raise PIIError("pii_input_too_large", 413)
    if mapping is not None and (
        len(mapping) > settings.pii_mapping_entries
        or len(json.dumps(mapping, ensure_ascii=False).encode())
        > settings.pii_mapping_bytes
    ):
        raise PIIError("pii_input_too_large", 413)
    if mapping is not None and any(
        not isinstance(key, str)
        or re.fullmatch(r"\[[A-Z][A-Z0-9_]*_\d{1,9}\]", key) is None
        or len(key) > 128
        or not isinstance(value, str)
        for key, value in mapping.items()
    ):
        raise PIIError("pii_invalid_mapping")


class IngressLimits:
    """Pure ASGI middleware: bound chunked bodies and retain accounting through send."""

    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.retained = self.requests = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse
        from faden_pii.services.pii.runtime import RequestBudget, request_budget
        import time

        async def refuse(status, code):
            return await JSONResponse({"detail": {"code": code}}, status_code=status)(
                scope, receive, send
            )

        if self.requests >= self.settings.pii_pending_requests:
            return await refuse(503, "pii_overloaded")
        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > self.settings.pii_body_bytes:
                return await refuse(413, "pii_input_too_large")
            milliseconds = min(
                float(headers.get(b"x-pii-budget-ms", b"8000")),
                self.settings.pii_work_timeout_seconds * 1000,
            )
            if not math.isfinite(milliseconds) or not 0 < milliseconds <= 10_000:
                return await refuse(422, "pii_invalid_deadline")
        except ValueError:
            return await refuse(422, "pii_invalid_deadline")
        if not math.isfinite(float(headers.get(b"x-pii-budget-ms", b"8000"))):
            return await refuse(422, "pii_invalid_deadline")
        self.requests += 1
        size = 0
        token = request_budget.set(
            RequestBudget(
                time.monotonic() + milliseconds / 1000,
                headers.get(b"x-pii-scope", b"").decode("latin1")[:128],
            )
        )
        try:
            chunks = []
            while True:
                try:
                    async with asyncio.timeout(
                        max(0, request_budget.get().deadline - time.monotonic())
                    ):
                        message = await receive()
                except TimeoutError:
                    return await refuse(408, "pii_body_timeout")
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                self.retained += len(chunk)
                if size > self.settings.pii_body_bytes:
                    return await refuse(413, "pii_input_too_large")
                if self.retained > self.settings.pii_pending_bytes:
                    return await refuse(503, "pii_overloaded")
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            chunks.clear()
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            response_started = False

            async def bounded_send(message):
                nonlocal response_started
                if message["type"] == "http.response.start":
                    response_started = True
                await send(message)

            try:
                # Include response backpressure in retained-request accounting.
                async with asyncio.timeout(
                    max(0, request_budget.get().deadline + 2 - time.monotonic())
                ):
                    await self.app(scope, replay, bounded_send)
            except TimeoutError:
                if not response_started:
                    return await refuse(504, "pii_deadline_exceeded")
        finally:
            request_budget.reset(token)
            self.retained -= size
            self.requests -= 1
