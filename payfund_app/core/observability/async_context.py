"""Technical metadata only. Delayed/retried work links to its origin in a new trace."""

import re
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from payfund_app.core.observability.context import (
    bind_request_id,
    get_request_id,
    reset_request_id,
)

_propagator = TraceContextTextMapPropagator()


def capture_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    if get_request_id():
        carrier["request_id"] = get_request_id()
    return carrier


@contextmanager
def processing_context(carrier, *, attempt=1):
    carrier = carrier if isinstance(carrier, dict) else {}
    # W3C propagator ignores malformed contexts. Only the bounded technical carrier is read.
    carrier = {
        k: v
        for k, v in carrier.items()
        if k in {"traceparent", "tracestate", "request_id"}
        and isinstance(v, str)
        and len(v) <= 512
    }
    parent = trace.get_current_span(_propagator.extract(carrier)).get_span_context()
    request_id = carrier.get("request_id", "")
    token = bind_request_id(
        request_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id) else None
    )
    try:
        with trace.get_tracer("diddipay.async").start_as_current_span(
            "message.process",
            context=Context(),
            kind=trace.SpanKind.CONSUMER,
            links=[trace.Link(parent)] if parent.is_valid else [],
            attributes={"messaging.attempt": attempt},
            record_exception=False,
        ):
            yield
    finally:
        reset_request_id(token)
