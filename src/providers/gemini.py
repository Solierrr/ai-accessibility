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
from src.providers.capture import CallCapture
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

    async def moderate(
        self,
        image: ValidatedImage,
        *,
        prompt: str | None = None,
        capture: CallCapture | None = None,
    ) -> ModerationObservation: ...

    async def caption(
        self,
        image: ValidatedImage,
        *,
        purpose: str,
        title: str,
        max_alt_chars: int,
        prompt: str | None = None,
        capture: CallCapture | None = None,
    ) -> CaptionSuggestion: ...


class GeminiVisionProvider:
    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.model_version = f"gemini/{settings.gemini_model}"
        self._client = client

    async def moderate(
        self,
        image: ValidatedImage,
        *,
        prompt: str | None = None,
        capture: CallCapture | None = None,
    ) -> ModerationObservation:
        data, flagged = await self._generate(
            image, prompt or MODERATION_PROMPT, capture
        )
        try:
            return ModerationObservation(VisionAssessment.model_validate(data), flagged)
        except ValidationError as exc:
            raise ProviderError("Resposta de moderação inválida") from exc

    async def caption(
        self,
        image: ValidatedImage,
        *,
        purpose: str,
        title: str,
        max_alt_chars: int,
        prompt: str | None = None,
        capture: CallCapture | None = None,
    ) -> CaptionSuggestion:
        async def generate_caption(*, retry: bool) -> CaptionSuggestion:
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
            data, flagged = await self._generate(image, text, capture)
            if flagged:
                raise ProviderBlocked("Filtro de segurança na descrição")
            try:
                return CaptionSuggestion.model_validate(data)
            except ValidationError as exc:
                raise ProviderError("Resposta de descrição inválida") from exc

        suggestion = await generate_caption(retry=False)
        if (
            not suggestion.uncertain
            and len(" ".join(suggestion.alt_text.split())) > max_alt_chars
        ):
            suggestion = await generate_caption(retry=True)
        return suggestion

    async def _generate(
        self,
        image: ValidatedImage,
        prompt: str,
        capture: CallCapture | None = None,
    ) -> tuple[object, bool]:
        record = capture.begin(prompt, self.model_version) if capture else None
        try:
            data, flagged, usage = await self._request(image, prompt)
        except ProviderError as exc:
            if record:
                record.fail(exc)
            raise
        if record:
            record.done(
                tokens_in=usage.get("promptTokenCount"),
                tokens_out=usage.get("candidatesTokenCount"),
            )
        return data, flagged

    def _request_body(self, image: ValidatedImage, prompt: str) -> dict:
        body = {
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
        model = self.settings.gemini_model
        if model.startswith("gemini-3.5-"):
            level = "minimal"
        elif model.startswith("gemini-3."):
            level = "low"
        else:
            level = None
        if level:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": level}
        return body

    async def _post(
        self, client: httpx.AsyncClient, request_body: dict
    ) -> httpx.Response:
        url = f"{_BASE_URL}/{self.settings.gemini_model}:generateContent"
        headers = {"x-goog-api-key": self.settings.google_api_key}
        timeout = httpx.Timeout(self.settings.gemini_timeout_seconds)
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

    async def _request(
        self, image: ValidatedImage, prompt: str
    ) -> tuple[object, bool, dict]:
        if not self.settings.google_api_key:
            raise ProviderError("Provedor não configurado")
        request_body = self._request_body(image, prompt)
        if self._client is None:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                response = await self._post(client, request_body)
        else:
            response = await self._post(self._client, request_body)
        return _parse_response(response)


def _candidate_text(candidate: dict) -> str:
    parts = candidate["content"]["parts"]
    return "".join(
        part.get("text", "") for part in parts if not part.get("thought", False)
    )


def _candidate_flagged(candidate: dict) -> bool:
    return any(
        rating.get("probability") in {"MEDIUM", "HIGH"} or rating.get("blocked") is True
        for rating in candidate.get("safetyRatings", [])
    )


def _parse_response(response: httpx.Response) -> tuple[object, bool, dict]:
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
        usage = payload.get("usageMetadata")
        return (
            json.loads(_candidate_text(candidate)),
            _candidate_flagged(candidate),
            usage if isinstance(usage, dict) else {},
        )
    except ProviderBlocked:
        raise
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ProviderError("Resposta inválida do provedor") from exc
