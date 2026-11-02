from typing import TypedDict

from src.agents.base.vision_agent import ModerationObservation, VisionProvider
from src.image.validation import ValidatedImage
from src.moderation.models import CaptionSuggestion
from src.moderation.policy import Decision


class ImageAnalysisState(TypedDict, total=False):
    # entrada
    content: bytes
    purpose: str
    max_alt_chars: int
    context_title: str
    request_id: str
    analysis_id: str
    # produzido pelas etapas
    image: ValidatedImage
    moderation_agent: VisionProvider
    observation: ModerationObservation
    suggestion: CaptionSuggestion
    used_models: list[str]
    # desfecho: quem decide a publicação é a política determinística
    decision: Decision
    reason_codes: tuple[str, ...]
    categories: tuple[str, ...]
    alt_text: str | None
