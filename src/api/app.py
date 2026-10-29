"""API interna para análise de imagem; não publica nem armazena arquivos."""

import asyncio
import hmac
import logging
import re
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.api.debug_access import authorize_debug
from src.api.upload_gate import UploadGate
from src.caption.validation import DEFAULT_ALT_TEXT_CHARS
from src.core.config import Settings
from src.image.validation import MAX_UPLOAD_BYTES, ImageValidationError, validate_image
from src.providers.capture import CallCapture
from src.providers.gemini import (
    GeminiVisionProvider,
    ProviderBlocked,
    ProviderError,
    VisionProvider,
)
from src.providers.groq import GroqVisionProvider
from src.providers.prompts import MODERATION_PROMPT, caption_prompt
from src.services.analysis import AnalysisResponse, analyze_image
from src.services.debug_runs import DebugRunStore, StoredRun
from src.services.trace import (
    StepRecorder,
    completion_summary,
    llm_calls,
)

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


class DebugAnalysisResponse(AnalysisResponse):
    debug: dict = Field(default_factory=dict)


class PromptMessage(BaseModel):
    type: str = Field(max_length=32)
    content: str


class PromptOverrideItem(BaseModel):
    call: int = Field(ge=0, le=50)
    prompt: list[PromptMessage]


class StageRequest(BaseModel):
    run_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    node: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    occurrence: int = Field(0, ge=0, le=10)
    stop_after_node: bool = True
    prompts: list[PromptOverrideItem] = Field(default_factory=list, max_length=10)


REPLAYABLE_STAGES = {"moderation": "policy", "caption": "caption_validation"}
STAGE_BLOCKED_REASON = (
    "Esta etapa não chama um modelo: não há prompt para editar. "
    "Só moderation e caption podem ser reexecutadas."
)


async def _run_stage(
    stage: str,
    stage_provider: VisionProvider,
    image,
    run: StoredRun,
    prompt: str | None,
    capture: CallCapture,
    recorder: StepRecorder,
) -> None:
    inputs: dict[str, object] = (
        {"mime_type": image.mime_type, "width": image.width, "height": image.height}
        if stage == "moderation"
        else {
            "purpose": run.purpose,
            "max_alt_chars": run.max_alt_chars,
            "context_title_chars": len(run.context_title),
        }
    )
    try:
        if stage == "moderation":
            observation = await stage_provider.moderate(
                image, prompt=prompt, capture=capture
            )
            signals = observation.assessment.model_dump(mode="json")
            recorder.add(
                stage,
                "ok",
                provider=stage_provider.model_version,
                safety_flagged=observation.safety_flagged,
                signals=signals,
                inputs=inputs,
                llm_calls=llm_calls(
                    capture,
                    completion_summary(
                        **signals, safety_flagged=observation.safety_flagged
                    ),
                ),
            )
            return
        suggestion = await stage_provider.caption(
            image,
            purpose=run.purpose,
            title=run.context_title,
            max_alt_chars=run.max_alt_chars,
            prompt=prompt,
            capture=capture,
        )
        recorder.add(
            stage,
            "ok",
            provider=stage_provider.model_version,
            uncertain=suggestion.uncertain,
            alt_text_chars=len(suggestion.alt_text),
            inputs=inputs,
            llm_calls=llm_calls(
                capture,
                completion_summary(
                    uncertain=suggestion.uncertain,
                    alt_text_chars=len(suggestion.alt_text),
                ),
            ),
        )
    except ProviderError as exc:
        recorder.add(
            stage,
            "blocked" if isinstance(exc, ProviderBlocked) else "error",
            provider=stage_provider.model_version,
            status_code=exc.status_code,
            cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None,
            inputs=inputs,
            llm_calls=llm_calls(capture, None),
        )


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
    runs = DebugRunStore()
    application = FastAPI(title="Solaria AI Accessibility", version="0.1.0")
    application.add_middleware(UploadGate, token=settings.internal_api_token)
    # Por último, para ficar por fora do UploadGate: o preflight não carrega
    # Authorization, e as respostas 401, 413 e 503 do gate também precisam dos headers CORS.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @application.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ready" if settings.ready else "not_configured"}

    async def run_analysis(
        image: UploadFile,
        purpose: Purpose,
        max_alt_chars: int,
        request_id: str,
        context_title: str,
        trace: StepRecorder | None = None,
    ) -> tuple[AnalysisResponse, bytes]:
        """Parte comum às rotas `analyze` e `debug_analyze`, depois da autenticação."""
        if not (settings.google_api_key or settings.groq_api_key):
            raise HTTPException(
                status_code=503, detail="Provedor de visão não configurado"
            )
        if request_id and not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", request_id):
            raise HTTPException(status_code=422, detail="request_id inválido")

        content = await image.read(MAX_UPLOAD_BYTES + 1)
        await image.close()
        async with semaphore:
            response = await analyze_image(
                content,
                provider=provider,
                fallback_provider=fallback_provider,
                request_id=request_id or str(uuid4()),
                purpose=purpose.value,
                max_alt_chars=max_alt_chars,
                context_title=context_title,
                trace=trace,
            )
        return response, content

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
        response, _ = await run_analysis(
            image, purpose, max_alt_chars, request_id, context_title
        )
        logger.info(
            "image_analysis analysis_id=%s request_id=%s decision=%s reasons=%s",
            response.analysis_id,
            response.request_id,
            response.decision,
            ",".join(response.reason_codes),
        )
        return response

    @application.post("/debug/images/analyze", include_in_schema=False)
    async def debug_analyze(
        image: Annotated[UploadFile, File()],
        purpose: Annotated[Purpose, Form()],
        max_alt_chars: Annotated[int, Form(ge=1)] = DEFAULT_ALT_TEXT_CHARS,
        request_id: Annotated[str, Form(max_length=100)] = "",
        context_title: Annotated[str, Form(max_length=120)] = "",
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> DebugAnalysisResponse:
        sub = authorize_debug(settings, credentials)
        logger.info(
            "Execução de debug (sub=%s, ambiente=%s)", sub, settings.environment
        )
        recorder = StepRecorder()
        response, content = await run_analysis(
            image, purpose, max_alt_chars, request_id, context_title, recorder
        )
        debug = recorder.snapshot()
        debug["environment"] = settings.environment
        debug["persisted"] = False
        debug["turn"] = 0
        if recorder.nodes[0]["output"]["outcome"] == "ok":
            debug["run_id"] = runs.add(
                StoredRun(
                    sub=sub,
                    content=content,
                    purpose=purpose.value,
                    max_alt_chars=max_alt_chars,
                    context_title=context_title,
                    attempts={
                        stage: recorder.providers(stage) for stage in REPLAYABLE_STAGES
                    },
                )
            )
        return DebugAnalysisResponse(**response.model_dump(), debug=debug)

    @application.post("/debug/images/stage", include_in_schema=False)
    async def debug_stage(
        payload: StageRequest,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> dict:
        sub = authorize_debug(settings, credentials)
        stage = next(
            (name for name in REPLAYABLE_STAGES if name == payload.node), "desconhecida"
        )
        logger.info(
            "Execução personalizada de etapa (sub=%s, etapa=%s, prompts=%s)",
            sub,
            stage,
            len(payload.prompts),
        )
        if payload.node not in REPLAYABLE_STAGES:
            raise HTTPException(status_code=422, detail=STAGE_BLOCKED_REASON)
        run = runs.get(sub, payload.run_id)
        if run is None:
            raise HTTPException(
                status_code=404,
                detail="Essa análise expirou ou não existe nesta instância. "
                "Rode a análise de novo.",
            )
        attempts = run.attempts.get(payload.node, [])
        if payload.occurrence >= len(attempts):
            raise HTTPException(
                status_code=404,
                detail=f"A etapa {payload.node} não rodou {payload.occurrence + 1}x.",
            )
        stage_provider = {
            p.model_version: p for p in (provider, fallback_provider) if p
        }.get(attempts[payload.occurrence])
        if stage_provider is None:
            raise HTTPException(
                status_code=409,
                detail="O provedor usado nessa análise não está mais configurado.",
            )
        try:
            image = validate_image(run.content)
        except ImageValidationError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc

        default_prompt = (
            MODERATION_PROMPT
            if payload.node == "moderation"
            else caption_prompt(
                purpose=run.purpose,
                title=run.context_title,
                max_alt_chars=run.max_alt_chars,
            )
        )
        edited = next(
            (
                message.content
                for item in payload.prompts
                if item.call == 0
                for message in item.prompt
                if message.type == "system"
            ),
            None,
        )
        applied = int(edited is not None and edited != default_prompt)
        capture = CallCapture()
        recorder = StepRecorder()
        async with semaphore:
            await _run_stage(
                payload.node, stage_provider, image, run, edited, capture, recorder
            )
        debug = recorder.snapshot()
        debug["environment"] = settings.environment
        debug["persisted"] = False
        debug["turn"] = 0
        debug["custom"] = {
            "run_id": run.run_id,
            "node": payload.node,
            "turn": 0,
            "occurrence": payload.occurrence,
            "stopped": True,
            "overrides_applied": applied,
            "next": [REPLAYABLE_STAGES[payload.node]],
        }
        return {"debug": debug}

    return application


app = create_app()
