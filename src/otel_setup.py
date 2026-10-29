"""OTLP metrics and traces for the HTTP service.

The SDK is opt-in and can be enabled by deployment configuration. Existing
application logs remain on their current path; no OTel log exporter is added.
"""

import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_providers: tuple[TracerProvider, MeterProvider] | None = None


def _redact_url(span, _request) -> None:
    """Keep HTTPX context propagation without exporting paths or query strings."""
    if span and span.is_recording():
        for key in ("url.full", "http.url", "url.path", "http.target"):
            span.set_attribute(key, "[REDACTED]")


async def _redact_async_url(span, request) -> None:
    _redact_url(span, request)


def _redact_server_url(span, _scope) -> None:
    if span and span.is_recording():
        for key in ("url.full", "http.url", "url.path", "http.target"):
            span.set_attribute(key, "[REDACTED]")


def configure_otel(app: FastAPI, service_name: str) -> None:
    """Instrument incoming FastAPI requests and outgoing HTTPX requests once."""
    global _providers
    if os.getenv("OTEL_SDK_DISABLED", "true").lower() != "false":
        return

    if _providers is None:
        endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"
        ).rstrip("/")
        environment = (
            os.getenv("DEPLOYMENT_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "local"
        ).lower()
        resource = Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": environment,
            }
        )
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
        # Boundaries in seconds include slower AI turns, so the +Inf bucket is
        # not the only bucket above 10 s when computing p50/p95/p99.
        bounds_seconds = [
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1,
            2,
            5,
            10,
            20,
            30,
            45,
            60,
            90,
            120,
            180,
            300,
        ]
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics")
                )
            ],
            views=[
                View(
                    instrument_name="http.server.request.duration",
                    aggregation=ExplicitBucketHistogramAggregation(bounds_seconds),
                ),
                View(
                    instrument_name="http.server.duration",
                    aggregation=ExplicitBucketHistogramAggregation(
                        [value * 1000 for value in bounds_seconds]
                    ),
                ),
            ],
        )
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        HTTPXClientInstrumentor().instrument(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            request_hook=_redact_url,
            async_request_hook=_redact_async_url,
        )
        _providers = tracer_provider, meter_provider

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_providers[0],
        meter_provider=_providers[1],
        server_request_hook=_redact_server_url,
        exclude_spans=["receive", "send"],
    )
