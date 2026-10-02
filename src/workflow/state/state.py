from typing import TypedDict

from src.agents.base.image_analysis_agent import ImageObservation
from src.image.validation import ValidatedImage
from src.moderation.policy import Decision


class ImageAnalysisState(TypedDict, total=False):
    content: bytes
    image: ValidatedImage
    purpose: str
    context_title: str
    max_alt_chars: int
    observation: ImageObservation
    used_models: list[str]
    decision: Decision
    reason_codes: tuple[str, ...]
    risk_categories: tuple[str, ...]
    alt_text: str | None
