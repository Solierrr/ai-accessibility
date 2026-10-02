from langchain_core.language_models.chat_models import BaseChatModel

from src.agents.base.image_analysis_agent import (
    ImageObservation,
    ProviderBlocked,
    ProviderError,
    ProviderInvalidOutput,
    ImageAnalysisAgent,
    ImageAnalysisProvider,
)
from src.core.config import Settings
from src.core.llm.llm_gemini import llm_gemini


class GeminiImageAnalysisProvider(ImageAnalysisAgent):
    def __init__(
        self,
        settings: Settings,
        model: BaseChatModel | None = None,
        *,
        api_key: str = "",
    ) -> None:
        super().__init__(
            model or llm_gemini(settings, api_key),
            model_version=f"gemini/{settings.gemini_model}",
            backend="gemini",
        )


__all__ = [
    "GeminiImageAnalysisProvider", "ImageObservation", "ProviderBlocked",
    "ProviderError", "ProviderInvalidOutput", "ImageAnalysisProvider",
]
