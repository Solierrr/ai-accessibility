from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from src.agents.base.image_analysis_agent import ImageAnalysisProvider
from src.caption.validation import DEFAULT_ALT_TEXT_CHARS
from src.moderation.policy import POLICY_VERSION, Decision
from src.workflow.graph.graph import build_analysis_graph
from src.workflow.runner import execute_image_analysis


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    analysis_id: str
    request_id: str
    decision: Decision
    reason_codes: list[str]
    risk_categories: list[str]
    alt_text: str | None
    policy_version: str
    model_version: str


async def analyze_image(
    content: bytes,
    *,
    provider: ImageAnalysisProvider,
    fallback_provider: ImageAnalysisProvider | None = None,
    request_id: str,
    purpose: str,
    max_alt_chars: int = DEFAULT_ALT_TEXT_CHARS,
    context_title: str = "",
    workflow=None,
) -> AnalysisResponse:
    """Executa o grafo sem persistir imagem ou estado entre requisições."""
    graph = workflow or build_analysis_graph(provider, fallback_provider)
    state = await execute_image_analysis(
        graph,
        content=content,
        purpose=purpose,
        context_title=context_title,
        max_alt_chars=max_alt_chars,
    )
    return AnalysisResponse(
        analysis_id=str(uuid4()),
        request_id=request_id,
        decision=state.get("decision", Decision.REVIEW_REQUIRED),
        reason_codes=list(state.get("reason_codes", ("ANALYSIS_INCOMPLETE",))),
        risk_categories=list(state.get("risk_categories", ())),
        alt_text=state.get("alt_text")
        if state.get("decision") == Decision.APPROVED
        else None,
        policy_version=POLICY_VERSION,
        model_version="+".join(state.get("used_models", [])) or provider.model_version,
    )
