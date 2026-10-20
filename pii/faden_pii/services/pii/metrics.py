"""Bounded metric names and values; never tenant IDs, source text or model errors."""

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily


class RuntimeCollector:
    def __init__(self, app):
        self.app = app

    def collect(self):
        service = getattr(self.app.state, "pii_service", None)
        if service is None:
            return
        runtime = service.runtime
        values = {
            "ready": int(runtime.healthy and not runtime.closed),
            "jobs": len(runtime.jobs),
            "retained_bytes": runtime.retained_bytes,
            "pending_tokens": runtime.pending_tokens,
            "pending_windows": runtime.pending_windows,
            "active_batches": runtime.stats["active_batches"],
            "max_padded_tokens": runtime.stats["max_padded_tokens"],
            "cache_bytes": service._analyses._bytes,
        }
        for name, value in values.items():
            yield GaugeMetricFamily(
                "pii_" + name, "Bounded PII runtime " + name, value=value
            )
        for name in (
            "batches",
            "windows",
            "singleflight_hits",
            "rejected",
            "deadlines",
            "owner_failures",
            "ooms",
            "recoveries",
            "recoveries_completed",
            "recovery_failures",
            "invalid_result_batches",
            "preparation_seconds",
            "inference_seconds",
        ):
            yield CounterMetricFamily(
                "pii_" + name, "PII runtime " + name, value=runtime.stats[name]
            )
        events = getattr(self.app.state, "pii_events", None)
        if events is not None:
            for name in ("dropped", "failed"):
                yield CounterMetricFamily(
                    "pii_events_" + name,
                    "PII event delivery " + name,
                    value=getattr(events, name),
                )
