from langchain_core.runnables import RunnableConfig

from src.moderation.policy import Decision
from src.workflow.nodes.context import agents_of, prompt_override
from src.workflow.nodes.stage_runner import StageFailure, run_stage
from src.workflow.observability.debug_tracker import completion_summary
from src.workflow.state.state import ImageAnalysisState


def moderation_inputs(image) -> dict[str, object]:
    return {"mime_type": image.mime_type, "width": image.width, "height": image.height}


async def moderation_node(state: ImageAnalysisState, config: RunnableConfig) -> dict:
    """Pede ao modelo os sinais de risco da imagem; quem decide é a política."""
    image = state["image"]
    prompt = prompt_override(config, "moderation")

    def signals(observation) -> dict:
        return observation.assessment.model_dump(mode="json")

    outcome = await run_stage(
        "moderation",
        agents_of(config),
        lambda agent, cfg: agent.moderate(image, prompt=prompt, config=cfg),
        config,
        inputs=moderation_inputs(image),
        summarize=lambda observation: {
            "safety_flagged": observation.safety_flagged,
            "signals": signals(observation),
        },
        completion=lambda observation: completion_summary(
            **signals(observation), safety_flagged=observation.safety_flagged
        ),
    )
    if isinstance(outcome, StageFailure):
        return {"decision": Decision.REVIEW_REQUIRED, "reason_codes": (outcome.reason,)}
    return {
        "observation": outcome.result,
        "moderation_agent": outcome.agent,
        "used_models": [outcome.agent.model_version],
    }


__all__ = ["moderation_inputs", "moderation_node"]
