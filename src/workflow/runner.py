from src.workflow.state.state import ImageAnalysisState


async def execute_image_analysis(
    workflow,
    *,
    content: bytes,
    purpose: str,
    context_title: str,
    max_alt_chars: int,
) -> ImageAnalysisState:
    return await workflow.ainvoke(
        {
            "content": content,
            "purpose": purpose,
            "context_title": context_title,
            "max_alt_chars": max_alt_chars,
            "used_models": [],
        }
    )
