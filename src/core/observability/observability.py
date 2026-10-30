"""
Configuração central de observabilidade da aplicação -> traces, métricas e logs via OTLP/HTTP para o otel-collector

Concentra na camada de infra a configuração do SDK OpenTelemetry -> observabilidade não se mistura com a camada
de negócio | equivale a ObservabilityConfig + TraceIdResponseFilter + OpenTelemetryAppenderInitializer do api-core

Modelo de referência SOLIER para FastAPI, mesmo contrato do api-core:
  * identidade -> service.name fixo por repo | SERVICE_VERSION | DEPLOYMENT_ENVIRONMENT
  * destino    -> OTEL_EXPORTER_OTLP_ENDPOINT (padrão http://localhost:4318, exporters somam /v1/<sinal>)
  * amostragem -> OTEL_TRACES_SAMPLING_PROBABILITY (padrão 100% | tail sampling decide no collector)
  * /health fora de traces e métricas (probes do k8s)
  * header X-Trace-Id em toda resposta
  * OTEL_SDK_DISABLED=true desliga tudo (testes)

Uso -> setup_observability(app, "nome-do-servico") logo após criar o FastAPI
"""

import logging
import os

from fastapi import FastAPI, Request
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode

TRACE_ID_HEADER = "X-Trace-Id"

# rotas de probe -> sem span e sem métrica (equivale ao noActuatorObservations do api-core)
_EXCLUDED_URLS = "health"

# mesmas faixas do SLO do api-core (50ms..5s) com piso 5ms e teto 10s | unidade ms da semconv antiga
_HTTP_BUCKETS_MS = (5, 50, 100, 200, 300, 500, 1000, 2000, 5000, 10000)

_LOG_FORMAT = (
    "%(asctime)s %(levelname)-5s [%(name)s] [trace=%(otelTraceID)s] %(message)s"
)

_configured = False


def setup_observability(app: FastAPI, service_name: str, **httpx_hooks) -> None:
    """Liga os 3 sinais OTLP e instrumenta a app | chamada única, idempotente

    httpx_hooks -> repassados ao HTTPXClientInstrumentor (ex.: request_hook que oculta chave de API)
    """
    global _configured
    if _configured or os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return
    _configured = True

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.getenv("SERVICE_VERSION", "dev"),
            "deployment.environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
        }
    )

    _setup_tracing(resource)
    _setup_metrics(resource)
    _setup_logging(resource)

    # server spans + métrica http.server.duration (nome lido pelos dashboards do collector)
    # exclude_spans -> sem os spans internos "http send/receive" do ASGI (ruído)
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls=_EXCLUDED_URLS, exclude_spans=["receive", "send"]
    )
    # propaga traceparent nas chamadas para outras APIs
    HTTPXClientInstrumentor().instrument(**httpx_hooks)

    @app.middleware("http")
    async def _trace_id_header(request: Request, call_next):
        response = await call_next(request)
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            response.headers[TRACE_ID_HEADER] = format(span_context.trace_id, "032x")
        return response


def mark_error(error: BaseException) -> None:
    """Marca o span atual como erro quando a exceção já foi tratada por um handler

    Sem isso, uma exceção convertida em resposta não aparece no trace -> o tail sampling do collector não guarda
    o trace de erro | só atualiza a observabilidade, não altera a resposta HTTP

    Equivale ao HttpObservationErrors do api-core | sem span ativo, não faz nada
    """
    span = trace.get_current_span()
    if span.get_span_context().is_valid:
        span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR, type(error).__name__))


def _setup_tracing(resource: Resource) -> None:
    probability = float(os.getenv("OTEL_TRACES_SAMPLING_PROBABILITY", "1.0"))
    provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(probability))
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def _setup_metrics(resource: Resource) -> None:
    http_buckets = View(
        instrument_name="http.server.duration",
        aggregation=ExplicitBucketHistogramAggregation(boundaries=_HTTP_BUCKETS_MS),
    )
    provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
        views=[http_buckets],
    )
    metrics.set_meter_provider(provider)


def _setup_logging(resource: Resource) -> None:
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)

    # instala o handler OTLP na raiz + injeta otelTraceID/otelSpanID em todo LogRecord
    # (equivale ao logback-spring.xml + OpenTelemetryAppenderInitializer do api-core)
    LoggingInstrumentor().instrument(inject_trace_context=True)

    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    # console com traceId, igual ao Spring
    if not any(type(h) is logging.StreamHandler for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(console)
