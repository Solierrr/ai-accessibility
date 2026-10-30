"""Contrato de erro da organização: todo 4xx e 5xx em application/problem+json."""

from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"

GENERIC_BY_STATUS = {
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    502: "DEPENDENCY_UNAVAILABLE",
    503: "DEPENDENCY_UNAVAILABLE",
}


class ProblemFieldError(BaseModel):
    field: str = Field(description="Campo com erro.", examples=["request_id"])
    errorCode: str = Field(
        description="Código estável do erro do campo.", examples=["VALIDATION_FAILED"]
    )
    detail: str = Field(
        description="Explicação para quem depura.", examples=["request_id inválido."]
    )


class ProblemDetail(BaseModel):
    type: str = Field(description="Sempre about:blank.", examples=["about:blank"])
    title: str = Field(
        description="O mesmo valor de errorCode.", examples=["VALIDATION_FAILED"]
    )
    status: int = Field(description="Código HTTP.", examples=[422])
    errorCode: str = Field(
        description="Código estável do catálogo de erros, traduzido pelo front.",
        examples=["VALIDATION_FAILED"],
    )
    detail: str = Field(
        description="Explicação para quem depura, em português; o front não exibe.",
        examples=["Erro de validação nos campos da requisição."],
    )
    instance: str = Field(
        description="Caminho da requisição.", examples=["/v1/images/analyze"]
    )
    timestamp: datetime = Field(description="Momento do erro, em UTC.")
    traceId: str | None = Field(
        default=None, description="Id do trace, quando houver instrumentação."
    )
    errors: list[ProblemFieldError] | None = Field(
        default=None, description="Erros por campo, só em validação."
    )


class ApiError(Exception):
    def __init__(self, status: int, error_code: str, detail: str, **extra):
        self.status = status
        self.error_code = error_code
        self.detail = detail
        self.extra = extra


def problem_body(
    path: str, status: int, error_code: str, detail: str, **extra
) -> dict[str, object]:
    return {
        "type": "about:blank",
        "title": error_code,
        "status": status,
        "errorCode": error_code,
        "detail": detail,
        "instance": path,
        "timestamp": datetime.now(UTC).isoformat(),
        **extra,
    }


def problem_response(
    path: str, status: int, error_code: str, detail: str, **extra
) -> JSONResponse:
    return JSONResponse(
        problem_body(path, status, error_code, detail, **extra),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def problem(
    request: Request, status: int, error_code: str, detail: str, **extra
) -> JSONResponse:
    return problem_response(request.url.path, status, error_code, detail, **extra)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return problem(request, exc.status, exc.error_code, exc.detail, **exc.extra)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = GENERIC_BY_STATUS.get(exc.status_code, "INTERNAL_ERROR")
        return problem(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {
                "field": ".".join(str(p) for p in e["loc"][1:]),
                "errorCode": "VALIDATION_FAILED",
                "detail": e["msg"],
            }
            for e in exc.errors()
        ]
        return problem(
            request,
            422,
            "VALIDATION_FAILED",
            "Erro de validação nos campos da requisição.",
            errors=errors,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return problem(
            request, 500, "INTERNAL_ERROR", "Ocorreu um erro interno inesperado."
        )
