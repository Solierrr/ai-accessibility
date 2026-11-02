from langchain_core.runnables import RunnableConfig

from src.caption.validation import validated_alt_text
from src.moderation.policy import Decision
from src.workflow.nodes.context import tracker_of
from src.workflow.state.state import ImageAnalysisState


async def caption_validation_node(
    state: ImageAnalysisState, config: RunnableConfig
) -> dict:
    """Valida a legenda localmente; sem texto válido, a imagem vai para revisão."""
    tracker = tracker_of(config)
    max_alt_chars = state["max_alt_chars"]
    alt_text = validated_alt_text(state["suggestion"], max_alt_chars=max_alt_chars)
    inputs = {"max_alt_chars": max_alt_chars}
    if alt_text is None:
        if tracker:
            tracker.add(
                "caption_validation",
                "invalid",
                max_alt_chars=max_alt_chars,
                inputs=inputs,
            )
        return {
            "decision": Decision.REVIEW_REQUIRED,
            "reason_codes": ("CAPTION_INVALID",),
        }
    if tracker:
        tracker.add(
            "caption_validation", "ok", alt_text_chars=len(alt_text), inputs=inputs
        )
    return {"decision": Decision.APPROVED, "alt_text": alt_text}
