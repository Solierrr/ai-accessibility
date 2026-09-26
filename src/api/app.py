"""API interna para análise de imagem; não publica nem armazena arquivos."""

import asyncio
import hmac
import logging
import re
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.upload_gate import UploadGate
from src.caption.validation import DEFAULT_ALT_TEXT_CHARS
from src.core.config import Settings
from src.image.validation import MAX_UPLOAD_BYTES
from src.providers.gemini import GeminiVisionProvider, VisionProvider
from src.providers.groq import GroqVisionProvider
from src.services.analysis import AnalysisResponse, analyze_image

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Token interno do ai-accessibility. No Swagger, informe só o valor; Bearer é acrescentado automaticamente.",
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
    application = FastAPI(title="Solaria AI Accessibility", version="0.1.0")
    application.add_middleware(UploadGate, token=settings.internal_api_token)

    @application.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ready" if settings.ready else "not_configured"}

    @application.post("/v1/images/analyze", response_model=AnalysisResponse)
    async def analyze(
        image: Annotated[UploadFile, File()],
        purpose: Annotated[Purpose, Form()],
        max_alt_chars: Annotated[int, Form(ge=1)] = DEFAULT_ALT_TEXT_CHARS,
        request_id: Annotated[str, Form(max_length=100)] = "",
        context_title: Annotated[str, Form(max_length=120)] = "",
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> AnalysisResponse:
        if not settings.internal_api_token:
            raise HTTPException(
                status_code=503, detail="Autenticação interna não configurada"
            )
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied, settings.internal_api_token):
            raise HTTPException(status_code=401, detail="Credencial interna inválida")
        if not (settings.google_api_key or settings.groq_api_key):
            raise HTTPException(
                status_code=503, detail="Provedor de visão não configurado"
            )
        if request_id and not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", request_id):
            raise HTTPException(status_code=422, detail="request_id inválido")

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
