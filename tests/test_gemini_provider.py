import asyncio
import unittest
from io import BytesIO

from langchain_core.messages import AIMessage
from PIL import Image

from src.core.config import Settings
from src.image.validation import validate_image
from src.moderation.models import ImageAnalysisSuggestion
from src.providers.gemini import (
    GeminiImageAnalysisProvider,
    ProviderBlocked,
    ProviderInvalidOutput,
)


def sample_image():
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="JPEG")
    return validate_image(output.getvalue())


def safe_suggestion() -> ImageAnalysisSuggestion:
    return ImageAnalysisSuggestion(
        sexual_content="none",
        graphic_violence="none",
        hateful_content="none",
        offensive_text="none",
        context="ordinary",
        confidence="high",
        prompt_injection=False,
        uncertain=False,
        is_safe=True,
        purpose_evidence="photovoltaic_product",
        person_presentation="not_applicable",
        scene_context="neutral",
        solar_visual_cue="panel",
        motivo_bloqueio=None,
        legenda_acessivel="Painel solar sobre o telhado.",
    )


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []
        self.options = []

    def with_structured_output(self, schema, **kwargs):
        self.options.append((schema, kwargs))
        return self

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return next(self.responses)


class GeminiProviderTests(unittest.TestCase):
    def test_single_call_sends_image_and_parses_both_signals(self) -> None:
        model = FakeModel([{
            "raw": AIMessage(content="", response_metadata={"finish_reason": "STOP"}),
            "parsed": safe_suggestion(),
            "parsing_error": None,
        }])
        provider = GeminiImageAnalysisProvider(Settings("test-key"), model)
        observation = asyncio.run(provider.analyze(
            sample_image(), purpose="product", title="", max_alt_chars=150,
        ))
        self.assertEqual(len(model.messages), 1)
        self.assertTrue(observation.suggestion.is_safe)
        self.assertEqual(observation.suggestion.legenda_acessivel, "Painel solar sobre o telhado.")
        image_part = model.messages[0][1].content[1]
        self.assertEqual(image_part["mime_type"], "image/jpeg")
        self.assertTrue(image_part["base64"])
        self.assertEqual(model.options[0][1]["method"], "json_schema")

    def test_safety_rating_requests_review(self) -> None:
        model = FakeModel([{
            "raw": AIMessage(content="", response_metadata={
                "finish_reason": "STOP",
                "safety_ratings": [{"probability": "MEDIUM"}],
            }),
            "parsed": safe_suggestion(),
            "parsing_error": None,
        }])
        provider = GeminiImageAnalysisProvider(Settings("test-key"), model)
        observation = asyncio.run(provider.analyze(
            sample_image(), purpose="product", title="", max_alt_chars=150,
        ))
        self.assertTrue(observation.safety_flagged)

    def test_block_and_invalid_output_never_look_safe(self) -> None:
        async def check(response, error_type):
            provider = GeminiImageAnalysisProvider(
                Settings("test-key"), FakeModel([response])
            )
            with self.assertRaises(error_type):
                await provider.analyze(
                    sample_image(), purpose="product", title="", max_alt_chars=150,
                )

        asyncio.run(check({
            "raw": AIMessage(content="", response_metadata={
                "prompt_feedback": {"block_reason": "SAFETY"},
            }),
            "parsed": None,
            "parsing_error": None,
        }, ProviderBlocked))
        asyncio.run(check({
            "raw": AIMessage(content="{}"),
            "parsed": None,
            "parsing_error": ValueError("invalid"),
        }, ProviderInvalidOutput))


if __name__ == "__main__":
    unittest.main()
