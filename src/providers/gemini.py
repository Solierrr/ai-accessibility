"""Adaptador HTTP para análise multimodal; nenhuma resposta autoriza publicação sozinha."""

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import ValidationError

from src.core.config import Settings
from src.image.validation import ValidatedImage
from src.moderation.models import CaptionSuggestion, VisionAssessment
from src.providers.prompts import MODERATION_PROMPT, caption_prompt

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)

class ProviderError(Exception):
    """Falha ou saída inválida do provedor; conteúdo original não aparece na mensagem."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderBlocked(ProviderError):
    """Filtro do provedor impediu resposta; requer revisão humana."""


@dataclass(frozen=True, slots=True)
class ModerationObservation:
    assessment: VisionAssessment
    safety_flagged: bool


class VisionProvider(Protocol):
    model_version: str

    async def moderate(self, image: ValidatedImage) -> ModerationObservation: ...

    async def caption(
        self, image: ValidatedImage, *, purpose: str, title: str, max_alt_chars: int
    ) -> CaptionSuggestion: ...


class GeminiVisionProvider:
    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.model_version = f"gemini/{settings.gemini_model}"
        self._client = client

    async def moderate(self, image: ValidatedImage) -> ModerationObservation:
        data, flagged = await self._generate(image, MODERATION_PROMPT)
        try:
            return ModerationObservation(VisionAssessment.model_validate(data), flagged)
        except ValidationError as exc:
            raise ProviderError("Resposta de moderação inválida") from exc

    async def caption(
        self, image: ValidatedImage, *, purpose: str, title: str, max_alt_chars: int
    ) -> CaptionSuggestion:
        async def generate_caption(*, retry: bool) -> CaptionSuggestion:
            prompt = caption_prompt(
                purpose=purpose,
                title=title,
                max_alt_chars=max_alt_chars,
                retry=retry,
            )
            data, flagged = await self._generate(image, prompt)
            if flagged:
                raise ProviderBlocked("Filtro de segurança na descrição")
            try:
                return CaptionSuggestion.model_validate(data)
            except ValidationError as exc:
                raise ProviderError("Resposta de descrição inválida") from exc

        suggestion = await generate_caption(retry=False)
        if not suggestion.uncertain and len(" ".join(suggestion.alt_text.split())) > max_alt_chars:
            suggestion = await generate_caption(retry=True)
        return suggestion

    async def _generate(
        self, image: ValidatedImage, prompt: str
    ) -> tuple[object, bool]:
        if not self.settings.google_api_key:
            raise ProviderError("Provedor não configurado")
        request_body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": image.mime_type,
                                "data": base64.b64encode(image.content).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": 4096,
            },
            "safetySettings": [
                {"category": category, "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
                for category in _SAFETY_CATEGORIES
            ],
        }
        if self.settings.gemini_model.startswith("gemini-3.5-"):
            request_body["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": "minimal"
            }
        elif self.settings.gemini_model.startswith("gemini-3."):
            request_body["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": "low"
            }
        url = f"{_BASE_URL}/{self.settings.gemini_model}:generateContent"
        headers = {"x-goog-api-key": self.settings.google_api_key}
        timeout = httpx.Timeout(self.settings.gemini_timeout_seconds)

        async def send(client: httpx.AsyncClient) -> httpx.Response:
            for attempt in range(2):
                try:
                    response = await client.post(
                        url, json=request_body, headers=headers, timeout=timeout
                    )
                except httpx.RequestError as exc:
                    if attempt == 0:
                        await asyncio.sleep(0.3)
                        continue
                    raise ProviderError("Falha de conexão com o provedor") from exc
                if response.status_code in {500, 502, 503, 504} and attempt == 0:
                    await asyncio.sleep(0.3)
                    continue
                if response.status_code >= 400:
                    raise ProviderError(
                        f"Provedor retornou HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                return response
            raise ProviderError("Provedor indisponível")

        if self._client is None:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                response = await send(client)
        else:
            response = await send(self._client)

        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("Resposta não é objeto")
            feedback = payload.get("promptFeedback") or {}
            if feedback.get("blockReason"):
                raise ProviderBlocked("Entrada bloqueada pelo provedor")
            candidates = payload.get("candidates") or []
            candidate = candidates[0]
            if candidate.get("finishReason") != "STOP":
                raise ProviderBlocked("Resposta incompleta ou bloqueada pelo provedor")
            parts = candidate["content"]["parts"]
            content = "".join(
                part.get("text", "") for part in parts if not part.get("thought", False)
            )
            flagged = any(
                rating.get("probability") in {"MEDIUM", "HIGH"}
                or rating.get("blocked") is True
                for rating in candidate.get("safetyRatings", [])
            )
            return json.loads(content), flagged
        except ProviderBlocked:
            raise
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError("Resposta inválida do provedor") from exc
