"""Reexecuta uma única etapa de modelo, com o prompt editado, para o modo debug."""

from src.agents.base.vision_agent import VisionProvider
from src.image.validation import ValidatedImage
from src.workflow.debug_runs import StoredRun
from src.workflow.nodes.caption_node import caption_node
from src.workflow.nodes.moderation_node import moderation_node
from src.workflow.observability.debug_tracker import StepRecorder

# etapa que pode ser reexecutada -> etapa seguinte do grafo
REPLAYABLE_STAGES = {"moderation": "policy", "caption": "caption_validation"}
STAGE_BLOCKED_REASON = (
    "Esta etapa não chama um modelo: não há prompt para editar. "
    "Só moderation e caption podem ser reexecutadas."
)


async def replay_stage(
    stage: str,
    agent: VisionProvider,
    image: ValidatedImage,
    run: StoredRun,
    prompt: str | None,
    recorder: StepRecorder,
) -> None:
    """Roda o mesmo nó do grafo com um só agente (sem fallback) e registra no `recorder`."""
    state = {
        "image": image,
        "purpose": run.purpose,
        "max_alt_chars": run.max_alt_chars,
        "context_title": run.context_title,
        "moderation_agent": agent,
    }
    config = {
        "callbacks": [recorder.capture],
        "configurable": {
            "agents": [agent],
            "tracker": recorder,
            "prompt_overrides": {stage: prompt} if prompt is not None else {},
        },
    }
    node = moderation_node if stage == "moderation" else caption_node
    await node(state, config)
