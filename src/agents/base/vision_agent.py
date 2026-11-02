"""Agente de visão sobre um modelo LangChain; nenhuma resposta autoriza publicação sozinha."""

import base64
from dataclasses import dataclass
from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError

from src.agents.specialist.caption.caption_prompt import caption_prompt
from src.agents.specialist.moderation.moderation_prompt import MODERATION_PROMPT
from src.image.validation import ValidatedImage
from src.moderation.models import CaptionSuggestion, VisionAssessment

_BLOCK_TOKENS = ("safety", "blocked", "content_filter", "contentfilter")
_BLOCK_FINISH = {"SAFETY", "CONTENT_FILTER", "PROHIBITED_CONTENT", "BLOCKED"}
_OK_FINISH = {"STOP", "FINISH_REASON_UNSPECIFIED", ""}
_USER_TEXT = "Analise esta imagem."


class ProviderError(Exception):
    """Falha ou saída inválida do provedor; conteúdo original não aparece na mensagem."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderBlocked(ProviderError):
    """Filtro do provedor impediu resposta; requer revisão humana e não aciona fallback."""


@dataclass(frozen=True, slots=True)
class ModerationObservation:
    assessment: VisionAssessment
    safety_flagged: bool


class VisionProvider(Protocol):
    model_version: str

    async def moderate(
        self,
        image: ValidatedImage,
        *,
        prompt: str | None = None,
        config: RunnableConfig | None = None,
    ) -> ModerationObservation: ...

    async def caption(
        self,
        image: ValidatedImage,
        *,
        purpose: str,
        title: str,
        max_alt_chars: int,
        prompt: str | None = None,
        config: RunnableConfig | None = None,
    ) -> CaptionSuggestion: ...


def _name(value: object) -> str:
    return str(getattr(value, "name", value)).split(".")[-1].upper()


def _safety_flagged(raw: object) -> bool:
    """Lê os metadados do provedor; levanta `ProviderBlocked` quando ele barrou a resposta."""
    metadata = {
        **(getattr(raw, "additional_kwargs", {}) or {}),
        **(getattr(raw, "response_metadata", {}) or {}),
    }
    feedback = metadata.get("prompt_feedback") or metadata.get("promptFeedback") or {}
    if isinstance(feedback, dict) and (
        feedback.get("block_reason") or feedback.get("blockReason")
    ):
        raise ProviderBlocked("Entrada bloqueada pelo provedor")
    finish = _name(metadata.get("finish_reason") or metadata.get("finishReason") or "")
    if finish in _BLOCK_FINISH:
        raise ProviderBlocked("Resposta bloqueada pelo provedor")
    if finish not in _OK_FINISH:
        raise ProviderBlocked("Resposta incompleta ou bloqueada pelo provedor")
    ratings = metadata.get("safety_ratings") or metadata.get("safetyRatings") or []
    for rating in ratings:
        if not isinstance(rating, dict):
            continue
        if rating.get("blocked") is True or _name(rating.get("probability", "")) in {
            "MEDIUM",
            "HIGH",
        }:
            return True
    return False


class VisionAgent:
    """Chama um modelo LangChain (Gemini ou GroqCloud) com saída estruturada em Pydantic."""

    def __init__(
        self, model: BaseChatModel, *, model_version: str, backend: str
    ) -> None:
        self.model = model
        self.model_version = model_version
        self.backend = backend

    async def moderate(
        self,
        image: ValidatedImage,
        *,
        prompt: str | None = None,
        config: RunnableConfig | None = None,
    ) -> ModerationObservation:
        parsed, flagged = await self._ask(
            VisionAssessment, prompt or MODERATION_PROMPT, image, config
        )
        return ModerationObservation(parsed, flagged)

    async def caption(
        self,
        image: ValidatedImage,
        *,
        purpose: str,
        title: str,
        max_alt_chars: int,
        prompt: str | None = None,
        config: RunnableConfig | None = None,
    ) -> CaptionSuggestion:
        async def generate(*, retry: bool) -> CaptionSuggestion:
            text = (
                prompt
                if prompt and not retry
                else caption_prompt(
                    purpose=purpose,
                    title=title,
                    max_alt_chars=max_alt_chars,
                    retry=retry,
                )
            )
            suggestion, flagged = await self._ask(
                CaptionSuggestion, text, image, config
            )
            if flagged:
                raise ProviderBlocked("Filtro de segurança na descrição")
            return suggestion

        suggestion = await generate(retry=False)
        if (
            not suggestion.uncertain
            and len(" ".join(suggestion.alt_text.split())) > max_alt_chars
        ):
            suggestion = await generate(retry=True)
        return suggestion

    def _messages(self, prompt: str, image: ValidatedImage) -> list:
        encoded = base64.b64encode(image.content).decode("ascii")
        if self.backend == "gemini":
            part = {"type": "image", "base64": encoded, "mime_type": image.mime_type}
        else:
            part = {
                "type": "image_url",
                "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"},
            }
        return [
            SystemMessage(content=prompt),
            HumanMessage(content=[{"type": "text", "text": _USER_TEXT}, part]),
        ]

    async def _ask[T: BaseModel](
        self,
        schema: type[T],
        prompt: str,
        image: ValidatedImage,
        config: RunnableConfig | None,
    ) -> tuple[T, bool]:
        structured = self.model.with_structured_output(
            schema, method="json_schema", include_raw=True
        )
        try:
            result = await structured.ainvoke(self._messages(prompt, image), config)
        except ProviderError:
            raise
        except Exception as exc:
            raise _translate(exc) from exc
        if not isinstance(result, dict):
            raise ProviderError("Resposta inválida do provedor")
        raw = result.get("raw")
        flagged = _safety_flagged(raw) if raw is not None else False
        if result.get("parsing_error") is not None or result.get("parsed") is None:
            raise ProviderError("Resposta estruturada inválida")
        try:
            return schema.model_validate(result["parsed"]), flagged
        except ValidationError as exc:
            raise ProviderError("Resposta estruturada inválida") from exc


def _translate(exc: Exception) -> ProviderError:
    name = type(exc).__name__.lower()
    detail = str(exc).lower()
    if any(token in name or token in detail for token in _BLOCK_TOKENS):
        return ProviderBlocked("Filtro de segurança do provedor")
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return ProviderError("Falha na chamada ao provedor", status_code=status)
