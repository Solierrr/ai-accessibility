"""Adaptador GroqCloud para fallback de análise visual."""

import asyncio
import base64
import json

import httpx
from pydantic import ValidationError

from src.core.config import Settings
from src.image.validation import ValidatedImage
from src.moderation.models import CaptionSuggestion, VisionAssessment
from src.providers.gemini import ModerationObservation, ProviderBlocked, ProviderError
from src.providers.prompts import MODERATION_PROMPT, caption_prompt

_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqVisionProvider:
    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.model_version = f"groq/{settings.groq_model}"
        self._client = client

    async def moderate(self, image: ValidatedImage) -> ModerationObservation:
        data = await self._generate(image, MODERATION_PROMPT)
        try:
            return ModerationObservation(VisionAssessment.model_validate(data), False)
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
            data = await self._generate(image, prompt)
            try:
                return CaptionSuggestion.model_validate(data)
            except ValidationError as exc:
                raise ProviderError("Resposta de descrição inválida") from exc

        suggestion = await generate_caption(retry=False)
        if not suggestion.uncertain and len(" ".join(suggestion.alt_text.split())) > max_alt_chars:
            suggestion = await generate_caption(retry=True)
        return suggestion

    async def _generate(self, image: ValidatedImage, prompt: str) -> object:
        if not self.settings.groq_api_key:
            raise ProviderError("Groq não configurado")
        encoded = base64.b64encode(image.content).decode("ascii")
        body = {
            "model": self.settings.groq_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image.mime_type};base64,{encoded}"
                            },
                        },
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 1024,
            "stream": False,
        }
        if self.settings.groq_model == "qwen/qwen3.8-27b":
            body["reasoning_effort"] = "none"
        headers = {"Authorization": f"Bearer {self.settings.groq_api_key}"}
        timeout = httpx.Timeout(self.settings.gemini_timeout_seconds)

        async def send(client: httpx.AsyncClient) -> httpx.Response:
            for attempt in range(2):
                try:
                    response = await client.post(
                        _URL, json=body, headers=headers, timeout=timeout
                    )
                except httpx.RequestError as exc:
                    if attempt == 0:
                        await asyncio.sleep(0.3)
                        continue
                    raise ProviderError("Falha de conexão com Groq") from exc
                if response.status_code in {500, 502, 503, 504} and attempt == 0:
                    await asyncio.sleep(0.3)
                    continue
                if response.status_code >= 400:
                    raise ProviderError(
                        f"Groq retornou HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                return response
            raise ProviderError("Groq indisponível")

        if self._client is None:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                response = await send(client)
        else:
            response = await send(self._client)

        try:
            payload = response.json()
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "content_filter" or choice["message"].get(
                "refusal"
            ):
                raise ProviderBlocked("Filtro de segurança do Groq")
            if choice.get("finish_reason") != "stop":
                raise ProviderError("Resposta incompleta do Groq")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Conteúdo inesperado")
            return json.loads(content)
        except ProviderBlocked:
            raise
        except ProviderError:
            raise
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError("Resposta inválida do Groq") from exc
