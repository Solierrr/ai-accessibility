"""
Centraliza o tratamento de exceções da API e devolve todo erro como ProblemDetail (RFC 9457)

Mesmo corpo do api-core -> web e mobile tratam erro de qualquer API com o mesmo parser

Modelo de referência SOLIER para FastAPI, espelha:
  * BusinessException + subclasses      -> ApiError + subclasses
  * ProblemDetailFactory                -> problem_detail()
  * GlobalExceptionHandler              -> register_error_handlers()
  * ProblemDetailAuthenticationEntryPoint / AccessDeniedHandler -> UnauthenticatedError / AccessDeniedError

Corpo (Content-Type application/problem+json):
  {"title": "Not Found", "status": 404, "detail": "...", "instance": "/rota",
   "errorCode": "<ERROR_CODE>", "timestamp": "...Z", "traceId": "<32 hex>", ...extras}

Logging -> 4xx WARNING só com a mensagem | 5xx ERROR com stack | toda exceção tratada marca o span como erro
"""

import logging
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from starlette.exceptions import HTTPException as StarletteHTTPException

from .observability import TRACE_ID_HEADER, mark_error

PROBLEM_JSON = "application/problem+json"

_log = logging.getLogger(__name__)

# códigos de erro HTTP "de framework" -> mesmos errorCode do api-core
_HTTP_ERROR_CODES = {
    401: "UNAUTHENTICATED",
    403: "ACCESS_DENIED",
    404: "RESOURCE_NOT_FOUND",
}


class ApiError(Exception):
    """Superclasse de todas as exceções de negócio da API | equivale ao BusinessException do api-core

    Carrega status HTTP, error_code e propriedades extras -> o handler global converte qualquer subclasse
    em ProblemDetail sem handler próprio

    Nova exceção = nova subclasse com status e error_code (UPPER_SNAKE) fixos
    """

    status: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, properties: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.properties = dict(properties or {})


class InvalidFieldError(ApiError):
    """Campo com valor inválido que a validação do Pydantic não cobre -> 400 INVALID_FIELD

    Validação de formato/obrigatoriedade fica no modelo Pydantic (VALIDATION_ERROR)
    """

    status, error_code = 400, "INVALID_FIELD"


class UnauthenticatedError(ApiError):
    """Token ausente, inválido ou expirado -> 401 UNAUTHENTICATED | equivale ao ProblemDetailAuthenticationEntryPoint"""

    status, error_code = 401, "UNAUTHENTICATED"


class AccessDeniedError(ApiError):
    """Usuário autenticado sem permissão -> 403 ACCESS_DENIED | equivale ao ProblemDetailAccessDeniedHandler"""

    status, error_code = 403, "ACCESS_DENIED"


class ResourceNotFoundError(ApiError):
    """Recurso pedido não existe -> 404 RESOURCE_NOT_FOUND

    Use as propriedades extras para devolver o identificador buscado
    """

    status, error_code = 404, "RESOURCE_NOT_FOUND"


class DuplicateResourceError(ApiError):
    """Recurso já existe -> 409 DUPLICATE_RESOURCE"""

    status, error_code = 409, "DUPLICATE_RESOURCE"


class BusinessRuleError(ApiError):
    """Regra de negócio violada -> 422 BUSINESS_RULE_VIOLATION"""

    status, error_code = 422, "BUSINESS_RULE_VIOLATION"


class DependencyUnavailableError(ApiError):
    """Serviço externo (LLM, Google, outra API) fora do ar ou respondendo erro -> 503 DEPENDENCY_UNAVAILABLE"""

    status, error_code = 503, "DEPENDENCY_UNAVAILABLE"


def problem_detail(
    status: int,
    detail: str,
    error_code: str,
    properties: dict[str, Any] | None,
    request: Request | None,
) -> dict[str, Any]:
    """Monta o corpo do erro | equivale ao ProblemDetailFactory.create do api-core

    title = reason phrase do status, como o Spring preenche quando não há title próprio
    """
    body: dict[str, Any] = {
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
    }
    if request is not None:
        body["instance"] = request.url.path
    body["errorCode"] = error_code
    body["timestamp"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        body["traceId"] = format(span_context.trace_id, "032x")
    if properties:
        body.update(properties)
    return body


def problem_response(
    status: int,
    detail: str,
    error_code: str,
    request: Request,
    properties: dict[str, Any] | None = None,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status,
        content=problem_detail(status, detail, error_code, properties, request),
        media_type=PROBLEM_JSON,
    )
    # o 500 sai pelo ServerErrorMiddleware, fora do middleware de X-Trace-Id -> header posto aqui também
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        response.headers[TRACE_ID_HEADER] = format(span_context.trace_id, "032x")
    return response


def register_error_handlers(app: FastAPI) -> None:
    """Registra os handlers globais | chamar depois de setup_observability"""
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)


def _record_error(error: BaseException, status: int, request: Request) -> None:
    """Marca o span como erro e registra log correlacionado ao trace"""
    mark_error(error)
    if status >= 500:
        _log.error(
            "Falha %d em %s %s",
            status,
            request.method,
            request.url.path,
            exc_info=error,
        )
    else:
        _log.warning(
            "Erro %d em %s %s: %s", status, request.method, request.url.path, error
        )


async def _api_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ApiError)
    _record_error(error, error.status, request)
    return problem_response(
        error.status, error.message, error.error_code, request, error.properties
    )


async def _validation_error(request: Request, error: Exception) -> JSONResponse:
    """Toda falha de validação do FastAPI -> 400 VALIDATION_ERROR + errors[{field, message}], formato do api-core

    field e message vêm do Pydantic sem tradução (ex.: "items.0.qty", "Field required") -> preserva o debug
    """
    assert isinstance(error, RequestValidationError)
    _record_error(error, 400, request)
    # loc[0] é a origem (body/query/path) -> fica fora do field
    errors = [
        {
            "field": ".".join(str(p) for p in e.get("loc", ())[1:]) or None,
            "message": e.get("msg"),
        }
        for e in error.errors()
    ]
    return problem_response(
        400,
        "Erro de validação nos campos da requisição.",
        "VALIDATION_ERROR",
        request,
        {"errors": errors},
    )


async def _http_error(request: Request, error: Exception) -> JSONResponse:
    # HTTPException do FastAPI/Starlette (inclui 404 de rota e 405) -> mesmo formato
    assert isinstance(error, StarletteHTTPException)
    status = error.status_code
    _record_error(error, status, request)
    error_code = _HTTP_ERROR_CODES.get(status) or HTTPStatus(status).name
    detail = (
        error.detail if isinstance(error.detail, str) else HTTPStatus(status).phrase
    )
    response = problem_response(status, detail, error_code, request)
    if error.headers:
        response.headers.update(error.headers)
    return response


async def _unexpected_error(request: Request, error: Exception) -> JSONResponse:
    # mensagem genérica -> nunca expõe detalhe interno para o cliente
    _record_error(error, 500, request)
    return problem_response(
        500,
        "Ocorreu um erro interno inesperado. Se persistir, contate o suporte.",
        "INTERNAL_ERROR",
        request,
    )
