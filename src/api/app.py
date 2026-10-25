"""API interna para análise de imagem; não publica nem armazena arquivos."""

import asyncio
import logging
import re
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.errors import ApiError, register_error_handlers
from src.api.openapi import (
    CONTACT,
    DESCRIPTION,
    LICENSE,
    SERVERS,
    SWAGGER_UI_PARAMETERS,
    TAGS,
    TITLE,
    install_openapi,
)
from src.api.service_auth import (
    SERVICE_KEY_HEADER,
    resolve_caller,
    service_key_header,
)
from src.api.upload_gate import UploadGate
from src.caption.validation import DEFAULT_ALT_TEXT_CHARS
from src.core.config import Settings
from src.image.validation import MAX_UPLOAD_BYTES
from src.providers.gemini import GeminiVisionProvider, VisionProvider
from src.providers.groq import GroqVisionProvider
from src.services.analysis import AnalysisResponse, analyze_image
from src.version import VERSION

logger = logging.getLogger(__name__)


class HealthStatus(BaseModel):
    status: str = Field(description="UP quando o processo responde.", examples=["UP"])
    version: str = Field(description="Versão do release.", examples=["0.2.0"])


class ReadyStatus(BaseModel):
    status: str = Field(
        description="UP com um provedor de visão configurado; DOWN (503) sem nenhum.",
        examples=["UP"],
    )


class Purpose(StrEnum):
    PRODUCT = "product"
    COMPANY_PROFILE = "company_profile"
    PROFESSIONAL_PROFILE = "professional_profile"
    OTHER = "other"


def create_app(
    settings: Settings | None = None,
    provider: VisionProvider | None = None,
    fallback_provider: VisionProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    provider = provider or (
        GeminiVisionProvider(settings)
        if settings.google_api_key
        else GroqVisionProvider(settings)
    )
    if fallback_provider is None and settings.google_api_key and settings.groq_api_key:
        fallback_provider = GroqVisionProvider(settings)
    semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
    application = FastAPI(
        title=TITLE,
        description=DESCRIPTION,
        version=VERSION,
        contact=CONTACT,
        license_info=LICENSE,
        servers=SERVERS,
        openapi_tags=TAGS,
        docs_url="/docs" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        redoc_url=None,
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    )
    install_openapi(application)
    register_error_handlers(application)
    analyze_callers = tuple(settings.service_keys)
    application.add_middleware(
        UploadGate, service_keys=settings.service_keys, callers=analyze_callers
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", SERVICE_KEY_HEADER],
    )

    @application.get(
        "/health",
        response_model=HealthStatus,
        tags=["Saúde"],
        summary="Indica se o processo está vivo",
        description="Responde enquanto o processo estiver de pé, sem consultar dependências.",
        operation_id="getHealth",
        openapi_extra={"security": [], "x-error-codes": ["INTERNAL_ERROR"]},
    )
    async def health() -> HealthStatus:
        return HealthStatus(status="UP", version=VERSION)

    @application.get(
        "/ready",
        response_model=ReadyStatus,
        tags=["Saúde"],
        summary="Indica se o serviço está pronto para atender",
        description="Pronto quando há ao menos um provedor de visão configurado.",
        operation_id="getReady",
        responses={503: {"model": ReadyStatus, "description": "Serviço não pronto"}},
        openapi_extra={"security": [], "x-error-codes": ["INTERNAL_ERROR"]},
    )
    async def ready() -> JSONResponse:
        if not settings.ready:
            return JSONResponse({"status": "DOWN"}, status_code=503)
        return JSONResponse({"status": "UP"})

    @application.post(
        "/v1/images/analyze",
        response_model=AnalysisResponse,
        tags=["Análise de imagens"],
        summary="Analisa uma imagem e sugere o texto alternativo",
        description=(
            "Valida a imagem, avalia o conteúdo e, quando aprovada, gera uma descrição "
            "curta em pt-BR. Imagem recusada ou sem descrição possível é resultado "
            "(decision rejected ou review_required), não erro. Aceita só os serviços "
            "chamadores configurados; nada é armazenado."
        ),
        operation_id="analyzeImage",
        responses={
            401: {"description": "Chave de serviço ausente ou inválida"},
            403: {"description": "Serviço chamador não autorizado nesta rota"},
            413: {"description": "Upload excede o limite"},
            422: {"description": "Campos do formulário inválidos"},
            503: {"description": "Provedor de visão não configurado"},
        },
        openapi_extra={
            "x-error-codes": [
                "UNAUTHENTICATED",
                "FORBIDDEN",
                "PAYLOAD_TOO_LARGE",
                "VALIDATION_FAILED",
                "DEPENDENCY_UNAVAILABLE",
                "INTERNAL_ERROR",
            ]
        },
    )
    async def analyze(
        image: Annotated[
            UploadFile, File(description="Imagem JPEG, PNG ou WebP, até 8 MiB.")
        ],
        purpose: Annotated[Purpose, Form(description="Onde a imagem será usada.")],
        max_alt_chars: Annotated[
            int, Form(ge=1, description="Tamanho máximo do texto alternativo.")
        ] = DEFAULT_ALT_TEXT_CHARS,
        request_id: Annotated[
            str,
            Form(
                max_length=100,
                description="Id de correlação do chamador ([A-Za-z0-9._:-]).",
            ),
        ] = "",
        context_title: Annotated[
            str, Form(max_length=120, description="Título do conteúdo, como contexto.")
        ] = "",
        service_key: Annotated[str | None, Security(service_key_header)] = None,
    ) -> AnalysisResponse:
        resolve_caller(service_key, settings.service_keys, analyze_callers)
        if not (settings.google_api_key or settings.groq_api_key):
            raise ApiError(
                503, "DEPENDENCY_UNAVAILABLE", "Provedor de visão não configurado."
            )
        if request_id and not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", request_id):
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "Erro de validação nos campos da requisição.",
                errors=[
                    {
                        "field": "request_id",
                        "errorCode": "VALIDATION_FAILED",
                        "detail": "request_id inválido.",
                    }
                ],
            )

        # Leitura limitada. O proxy HTTP de produção também deve limitar o corpo da requisição.
        content = await image.read(MAX_UPLOAD_BYTES + 1)
        await image.close()
        correlation = request_id or str(uuid4())
        async with semaphore:
            response = await analyze_image(
                content,
                provider=provider,
                fallback_provider=fallback_provider,
                request_id=correlation,
                purpose=purpose.value,
                max_alt_chars=max_alt_chars,
                context_title=context_title,
            )
        logger.info(
            "image_analysis analysis_id=%s request_id=%s decision=%s reasons=%s",
            response.analysis_id,
            response.request_id,
            response.decision,
            ",".join(response.reason_codes),
        )
        return response

    return application


app = create_app()
