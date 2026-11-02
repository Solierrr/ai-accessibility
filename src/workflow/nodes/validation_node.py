from langchain_core.runnables import RunnableConfig

from src.image.validation import ImageValidationError, validate_image
from src.moderation.policy import Decision
from src.workflow.nodes.context import tracker_of
from src.workflow.state.state import ImageAnalysisState


async def validation_node(state: ImageAnalysisState, config: RunnableConfig) -> dict:
    """Valida e normaliza a imagem antes de qualquer chamada a modelo."""
    tracker = tracker_of(config)
    content = state["content"]
    try:
        image = validate_image(content)
    except ImageValidationError as exc:
        if tracker:
            tracker.add(
                "image_validation",
                "rejected",
                code=exc.code,
                inputs={"bytes": len(content)},
            )
        return {"decision": Decision.REJECTED, "reason_codes": (exc.code,)}
    if tracker:
        tracker.add(
            "image_validation",
            "ok",
            mime_type=image.mime_type,
            width=image.width,
            height=image.height,
            inputs={"bytes": len(content)},
        )
    return {"image": image}
