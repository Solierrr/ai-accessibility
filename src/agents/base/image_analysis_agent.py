import base64
from dataclasses import dataclass
from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from src.agents.specialist.image_analysis.prompt import analysis_prompt
from src.image.validation import ValidatedImage
from src.moderation.models import ImageAnalysisSuggestion


class ProviderError(Exception):
    """Falha técnica do provedor, sem conteúdo da imagem na mensagem."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderBlocked(ProviderError):
    """Bloqueio de segurança; não aciona fallback."""


class ProviderInvalidOutput(ProviderError):
    """Saída inválida; não aciona fallback nem libera publicação."""


@dataclass(frozen=True, slots=True)
class ImageObservation:
    suggestion: ImageAnalysisSuggestion
    safety_flagged: bool


class ImageAnalysisProvider(Protocol):
    model_version: str

    async def analyze(
        self, image: ValidatedImage, *, purpose: str, title: str, max_alt_chars: int
    ) -> ImageObservation: ...


def _safety_metadata(raw: object) -> bool:
    metadata = {
        **(getattr(raw, "additional_kwargs", {}) or {}),
        **(getattr(raw, "response_metadata", {}) or {}),
    }
    feedback = metadata.get("prompt_feedback") or metadata.get("promptFeedback") or {}
    if isinstance(feedback, dict) and (
        feedback.get("block_reason") or feedback.get("blockReason")
    ):
        raise ProviderBlocked("Filtro de segurança do provedor")

    finish_raw = metadata.get("finish_reason") or metadata.get("finishReason") or ""
    finish = str(getattr(finish_raw, "name", finish_raw)).split(".")[-1].upper()
    if finish in {"SAFETY", "CONTENT_FILTER", "PROHIBITED_CONTENT", "BLOCKED"}:
        raise ProviderBlocked("Filtro de segurança do provedor")
    if finish and finish not in {"STOP", "FINISH_REASON_UNSPECIFIED"}:
        raise ProviderInvalidOutput("Resposta incompleta do provedor")

    ratings = metadata.get("safety_ratings") or metadata.get("safetyRatings") or []
    for rating in ratings:
        if not isinstance(rating, dict):
            continue
        probability = rating.get("probability", "")
        level = str(getattr(probability, "name", probability)).split(".")[-1].upper()
        if rating.get("blocked") is True or level in {"MEDIUM", "HIGH"}:
            return True
    return False


class ImageAnalysisAgent:
    """Agente LangChain com saída Pydantic para Gemini ou GroqCloud."""

    def __init__(self, model: BaseChatModel, *, model_version: str, backend: str) -> None:
        self.model = model
        self.model_version = model_version
        self.backend = backend
        self.structured_model = model.with_structured_output(
            ImageAnalysisSuggestion,
            method="json_schema",
            include_raw=True,
        )

    async def analyze(
        self, image: ValidatedImage, *, purpose: str, title: str, max_alt_chars: int
    ) -> ImageObservation:
        encoded = base64.b64encode(image.content).decode("ascii")
        if self.backend == "gemini":
            image_part = {
                "type": "image", "base64": encoded, "mime_type": image.mime_type,
            }
        else:
            image_part = {
                "type": "image_url",
                "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"},
            }
        user_text = (
            f"Título de contexto não confiável: {title}"
            if title else "Analise esta imagem."
        )
        messages = [
            SystemMessage(content=analysis_prompt(
                purpose=purpose, max_alt_chars=max_alt_chars,
            )),
            HumanMessage(content=[{"type": "text", "text": user_text}, image_part]),
        ]
        try:
            result = await self.structured_model.ainvoke(messages)
        except Exception as exc:
            name = type(exc).__name__.lower()
            detail = str(exc).lower()
            if any(token in name or token in detail for token in (
                "safety", "blocked", "content_filter", "contentfilter",
            )):
                raise ProviderBlocked("Filtro de segurança do provedor") from exc
            if isinstance(exc, ValidationError) or "parser" in name:
                raise ProviderInvalidOutput("Saída estruturada inválida") from exc
            status = getattr(exc, "status_code", None)
            if status is None:
                status = getattr(getattr(exc, "response", None), "status_code", None)
            raise ProviderError("Falha na chamada ao provedor", status_code=status) from exc

        if not isinstance(result, dict):
            raise ProviderInvalidOutput("Saída estruturada ausente")
        raw = result.get("raw")
        flagged = _safety_metadata(raw) if raw is not None else False
        if result.get("parsing_error") is not None or result.get("parsed") is None:
            raise ProviderInvalidOutput("Saída estruturada inválida")
        try:
            suggestion = ImageAnalysisSuggestion.model_validate(result["parsed"])
        except ValidationError as exc:
            raise ProviderInvalidOutput("Saída estruturada inválida") from exc
        return ImageObservation(suggestion, flagged)


class UnavailableImageAnalysisProvider:
    """Permite iniciar /health sem chaves de IA configuradas."""

    model_version = "unconfigured"

    async def analyze(
        self, image: ValidatedImage, *, purpose: str, title: str, max_alt_chars: int
    ) -> ImageObservation:
        raise ProviderError("Provedor não configurado")
