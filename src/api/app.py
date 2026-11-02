"""API interna para análise de imagem; não publica nem armazena arquivos."""

import asyncio
import logging
import re
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.agents.base.vision_agent import VisionProvider
from src.agents.factory.vision_agents import build_vision_agents
from src.agents.specialist.caption.caption_prompt import caption_prompt
from src.agents.specialist.moderation.moderation_prompt import MODERATION_PROMPT
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
from src.api.upload_gate import UploadGate
from src.caption.validation import DEFAULT_ALT_TEXT_CHARS
from src.core.config.settings import Settings
from src.core.observability.observability import setup_observability
from src.core.security.debug_access import authorize_debug
from src.core.security.service_auth import (
    SERVICE_KEY_HEADER,
    resolve_caller,
    service_key_header,
)
from src.image.validation import MAX_UPLOAD_BYTES, ImageValidationError, validate_image
from src.services.analysis import AnalysisResponse, analyze_image
from src.version import VERSION
from src.workflow.debug_replay import (
    REPLAYABLE_STAGES,
    STAGE_BLOCKED_REASON,
    replay_stage,
)
from src.workflow.debug_runs import DebugRunStore, StoredRun
from src.workflow.observability.debug_tracker import StepRecorder

logger = logging.getLogger(__name__)
# JWT de administrador, só nas rotas de debug (fora do contrato)
bearer_scheme = HTTPBearer(auto_error=False)


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


def create_app(
    settings: Settings | None = None,
    provider: VisionProvider | None = None,
    fallback_provider: VisionProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if provider is None:
        configured = build_vision_agents(settings)
        provider = configured[0] if configured else None
        if fallback_provider is None and len(configured) > 1:
            fallback_provider = configured[1]
    semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
    runs = DebugRunStore()
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
    # logo após criar a app, nesta ordem -> os erros usam o span criado pela observabilidade
    setup_observability(application, "ai-accessibility")
    install_openapi(application)
    register_error_handlers(application)
    analyze_callers = tuple(settings.service_keys)
    application.add_middleware(
        UploadGate, service_keys=settings.service_keys, callers=analyze_callers
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", SERVICE_KEY_HEADER],
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
        if provider is None:
            return JSONResponse({"status": "DOWN"}, status_code=503)
        return JSONResponse({"status": "UP"})

    async def run_analysis(
        image: UploadFile,
        purpose: Purpose,
        max_alt_chars: int,
        request_id: str,
        context_title: str,
        trace: StepRecorder | None = None,
    ) -> tuple[AnalysisResponse, bytes]:
        """Parte comum às rotas `analyze` e `debug_analyze`, depois da autenticação."""
        if provider is None:
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
        recorder = StepRecorder()
        async with semaphore:
            await replay_stage(
                payload.node, stage_provider, image, run, edited, recorder
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
