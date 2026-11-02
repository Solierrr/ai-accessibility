from langchain_core.runnables import RunnableConfig

from src.moderation.policy import Decision
from src.workflow.nodes.context import agents_of, prompt_override
from src.workflow.nodes.stage_runner import StageFailure, run_stage
from src.workflow.observability.debug_tracker import completion_summary
from src.workflow.state.state import ImageAnalysisState


def caption_inputs(
    purpose: str, max_alt_chars: int, context_title: str
) -> dict[str, object]:
    return {
        "purpose": purpose,
        "max_alt_chars": max_alt_chars,
        "context_title_chars": len(context_title),
    }


def _agents_for_caption(state: ImageAnalysisState, config: RunnableConfig) -> list:
    """O modelo que moderou legenda primeiro; o fallback só vem depois dele."""
    agents = list(agents_of(config))
    first = state.get("moderation_agent")
    if first is None:
        return agents
    versions = [agent.model_version for agent in agents]
    start = (
        versions.index(first.model_version) if first.model_version in versions else 0
    )
    return agents[start:] or [first]


async def caption_node(state: ImageAnalysisState, config: RunnableConfig) -> dict:
    """Gera o texto alternativo em pt-BR, só para imagens que a política aprovou."""
    image = state["image"]
    purpose, title = state["purpose"], state["context_title"]
    max_alt_chars = state["max_alt_chars"]
    prompt = prompt_override(config, "caption")

    outcome = await run_stage(
        "caption",
        _agents_for_caption(state, config),
        lambda agent, cfg: agent.caption(
            image,
            purpose=purpose,
            title=title,
            max_alt_chars=max_alt_chars,
            prompt=prompt,
            config=cfg,
        ),
        config,
        inputs=caption_inputs(purpose, max_alt_chars, title),
        summarize=lambda suggestion: {
            "uncertain": suggestion.uncertain,
            "alt_text_chars": len(suggestion.alt_text),
        },
        completion=lambda suggestion: completion_summary(
            uncertain=suggestion.uncertain, alt_text_chars=len(suggestion.alt_text)
        ),
    )
    if isinstance(outcome, StageFailure):
        return {"decision": Decision.REVIEW_REQUIRED, "reason_codes": (outcome.reason,)}
    used = list(state.get("used_models", []))
    if outcome.agent.model_version not in used:
        used.append(outcome.agent.model_version)
    return {"suggestion": outcome.result, "used_models": used}
