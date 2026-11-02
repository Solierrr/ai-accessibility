from langchain_core.runnables import RunnableConfig

from src.moderation.policy import Decision
from src.workflow.state.state import ImageAnalysisState


async def consolidation_node(state: ImageAnalysisState, config: RunnableConfig) -> dict:
    """Fecha o desfecho: todo caminho do grafo termina aqui, sempre falhando para revisão ou rejeição."""
    decision = state.get("decision", Decision.REVIEW_REQUIRED)
    approved = decision == Decision.APPROVED
    return {
        "decision": decision,
        "reason_codes": state.get("reason_codes", ()),
        "categories": state.get("categories", ()),
        "alt_text": state.get("alt_text") if approved else None,
    }
