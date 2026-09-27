"""Validação do formato REST e das respostas de segurança do provedor."""

import asyncio
import json
import unittest
from io import BytesIO

import httpx
from PIL import Image

from src.core.config import Settings
from src.image.validation import validate_image
from src.providers.gemini import GeminiVisionProvider, ProviderBlocked, ProviderError


def sample_image():
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="JPEG")
    return validate_image(output.getvalue())


class GeminiProviderTests(unittest.TestCase):
    def test_short_caption_retries_when_first_exceeds_limit(self) -> None:
        prompts = []

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            prompts.append(body["contents"][0]["parts"][0]["text"])
            caption = "Painéis solares sobre telhado" if len(prompts) == 1 else "Painel sol"
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {"alt_text": caption, "uncertain": False}
                                        )
                                    }
                                ]
                            },
                        }
                    ]
                },
            )

        async def run() -> None:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                provider = GeminiVisionProvider(Settings("test-key", "test-secret"), client)
                suggestion = await provider.caption(
                    sample_image(), purpose="product", title="", max_alt_chars=10
                )
                self.assertEqual(suggestion.alt_text, "Painel sol")

        asyncio.run(run())
        self.assertEqual(len(prompts), 2)
        self.assertIn("no máximo 10 caracteres", prompts[0])
        self.assertIn("uma ou duas palavras", prompts[0])
        self.assertIn("A resposta anterior excedeu", prompts[1])

    def test_sends_image_and_parses_structured_signals(self) -> None:
        seen = []

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body)
            self.assertEqual(request.headers["x-goog-api-key"], "test-key")
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "sexual_content": "none",
                                                "graphic_violence": "none",
                                                "hateful_content": "none",
                                                "offensive_text": "none",
                                                "context": "ordinary",
                                                "confidence": "high",
                                                "prompt_injection": False,
                                                "uncertain": False,
                                            }
                                        )
                                    }
                                ]
                            },
                            "safetyRatings": [{"probability": "MEDIUM"}],
                        }
                    ]
                },
            )

        async def run() -> None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle)
            ) as client:
                provider = GeminiVisionProvider(
                    Settings("test-key", "test-secret"), client
                )
                observation = await provider.moderate(sample_image())
                self.assertTrue(observation.safety_flagged)
                self.assertEqual(observation.assessment.confidence, "high")

        asyncio.run(run())
        self.assertEqual(
            seen[0]["contents"][0]["parts"][1]["inlineData"]["mimeType"], "image/jpeg"
        )

    def test_block_and_invalid_output_never_look_safe(self) -> None:
        async def check(response: dict, error_type: type[Exception]) -> None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=response)
                )
            ) as client:
                provider = GeminiVisionProvider(
                    Settings("test-key", "test-secret"), client
                )
                with self.assertRaises(error_type):
                    await provider.moderate(sample_image())

        asyncio.run(
            check({"promptFeedback": {"blockReason": "SAFETY"}}, ProviderBlocked)
        )
        asyncio.run(
            check(
                {
                    "candidates": [
                        {"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}
                    ]
                },
                ProviderError,
            )
        )


if __name__ == "__main__":
    unittest.main()
