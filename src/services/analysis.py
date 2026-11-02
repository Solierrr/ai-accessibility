"""Contrato da resposta e invocação do grafo; sempre falha para revisão ou rejeição."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from src.agents.base.vision_agent import VisionProvider
from src.caption.validation import DEFAULT_ALT_TEXT_CHARS
from src.moderation.policy import POLICY_VERSION, Decision
from src.workflow.graph.graph import analysis_graph
from src.workflow.observability.debug_tracker import StepRecorder


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    analysis_id: str = Field(
        description="Id da análise.", examples=["0b6f3c1e-2d4a-4f7e-9c1a-5e8d7b2a1f30"]
    )
    request_id: str = Field(
        description="Id de correlação recebido ou gerado.", examples=["upload-1"]
    )
    decision: Decision = Field(description="Resultado da análise.")
    reason_codes: list[str] = Field(
        description="Motivos da decisão; vazio quando aprovada.",
        examples=[["PROVIDER_UNAVAILABLE"]],
    )
    risk_categories: list[str] = Field(
        description="Categorias de risco identificadas.", examples=[[]]
    )
    alt_text: str | None = Field(
        description="Texto alternativo sugerido, só quando aprovada.",
        examples=["Painel solar azul sobre uma mesa."],
    )
    policy_version: str = Field(description="Versão da política de moderação.")
    model_version: str = Field(description="Modelos usados na análise.")


async def analyze_image(
    content: bytes,
    *,
    provider: VisionProvider,
    fallback_provider: VisionProvider | None = None,
    request_id: str,
    purpose: str,
    max_alt_chars: int = DEFAULT_ALT_TEXT_CHARS,
    context_title: str = "",
    trace: StepRecorder | None = None,
) -> AnalysisResponse:
    agents = [provider] if fallback_provider is None else [provider, fallback_provider]
    configurable: dict = {"agents": agents, "tracker": trace}
    config: dict = {"configurable": configurable}
    if trace is not None:
        config["callbacks"] = [trace.capture]
    analysis_id = str(uuid4())
    final = await analysis_graph.ainvoke(
        {
            "content": content,
            "purpose": purpose,
            "max_alt_chars": max_alt_chars,
            "context_title": context_title,
            "request_id": request_id,
            "analysis_id": analysis_id,
        },
        config,
    )
    return AnalysisResponse(
        analysis_id=analysis_id,
        request_id=request_id,
        decision=final["decision"],
        reason_codes=list(final["reason_codes"]),
        risk_categories=list(final["categories"]),
        alt_text=final["alt_text"],
        policy_version=POLICY_VERSION,
        model_version="+".join(final.get("used_models", [])) or provider.model_version,
    )
