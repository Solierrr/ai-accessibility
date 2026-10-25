"""API interna para análise de imagem; não publica nem armazena arquivos."""

import asyncio
import logging
import re
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, Security, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.errors import ApiError, register_error_handlers
from src.api.service_auth import resolve_caller, service_key_header
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
    application = FastAPI(title="Solaria AI Accessibility", version=VERSION)
    register_error_handlers(application)
    analyze_callers = tuple(settings.service_keys)
    application.add_middleware(
        UploadGate, service_keys=settings.service_keys, callers=analyze_callers
    )

    @application.get("/health", response_model=HealthStatus)
    async def health() -> HealthStatus:
        return HealthStatus(status="UP", version=VERSION)

    @application.get("/ready", response_model=ReadyStatus)
    async def ready() -> JSONResponse:
        if not settings.ready:
            return JSONResponse({"status": "DOWN"}, status_code=503)
        return JSONResponse({"status": "UP"})

    @application.post("/v1/images/analyze", response_model=AnalysisResponse)
    async def analyze(
        image: Annotated[UploadFile, File()],
        purpose: Annotated[Purpose, Form()],
        max_alt_chars: Annotated[int, Form(ge=1)] = DEFAULT_ALT_TEXT_CHARS,
        request_id: Annotated[str, Form(max_length=100)] = "",
        context_title: Annotated[str, Form(max_length=120)] = "",
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
