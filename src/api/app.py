import asyncio
import logging
import re
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.agents.base.image_analysis_agent import UnavailableImageAnalysisProvider
from src.api.auth import ApiAuthTokenVerifier
from src.api.upload_gate import UploadGate
from src.caption.validation import DEFAULT_ALT_TEXT_CHARS
from src.clients.registry import RegistryClient, RegistryConfig
from src.core.config import Settings
from src.image.validation import MAX_UPLOAD_BYTES
from src.providers.gemini import ImageAnalysisProvider
from src.providers.registry import RegistryImageAnalysisProvider
from src.services.analysis import AnalysisResponse, analyze_image
from src.workflow.graph.graph import build_analysis_graph

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="Access token JWT emitido pelo api-auth. No Swagger, informe só o valor.",
)


class Purpose(StrEnum):
    PRODUCT = "product"
    COMPANY_PROFILE = "company_profile"
    PROFESSIONAL_PROFILE = "professional_profile"


def create_app(
    settings: Settings | None = None,
    provider: ImageAnalysisProvider | None = None,
    fallback_provider: ImageAnalysisProvider | None = None,
    auth_verifier: ApiAuthTokenVerifier | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    provider = provider or (
        RegistryImageAnalysisProvider(
            settings,
            RegistryClient(
                RegistryConfig(
                    base_url=settings.registry_url or "",
                    token=settings.registry_token or "",
                    timeout_seconds=settings.registry_timeout_seconds,
                )
            ),
        )
        if settings.registry_configured
        else UnavailableImageAnalysisProvider()
    )
    workflow = build_analysis_graph(provider, fallback_provider)
    semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
    application = FastAPI(title="Solaria AI Accessibility", version="0.1.0")
    application.add_middleware(
        UploadGate,
        verifier=auth_verifier or ApiAuthTokenVerifier(
            settings.jwt_jwks_url, settings.jwt_issuer
        ),
    )

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
        if not settings.registry_configured:
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
                workflow=workflow,
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
