from langchain_core.language_models.chat_models import BaseChatModel

from src.agents.base.image_analysis_agent import ImageAnalysisAgent
from src.core.config import Settings
from src.core.llm.llm_groq import llm_groq


class GroqImageAnalysisProvider(ImageAnalysisAgent):
    def __init__(self, settings: Settings, model: BaseChatModel | None = None) -> None:
        super().__init__(
            model or llm_groq(settings),
            model_version=f"groq/{settings.groq_model}",
            backend="groq",
        )
