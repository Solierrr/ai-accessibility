"""Agente de visão sobre um modelo LangChain: mensagens, segurança e saída inválida."""

import asyncio
import unittest
from io import BytesIO

from langchain_core.callbacks import AsyncCallbackHandler
from PIL import Image

from src.agents.base.vision_agent import (
    ProviderBlocked,
    ProviderError,
    VisionAgent,
)
from src.agents.factory.vision_agents import build_vision_agents
from src.core.config.settings import Settings
from src.core.llm.keys import llm_environment, vision_providers
from src.image.validation import validate_image
from src.workflow.observability.debug_tracker import LlmCallCapture
from tests.fakes import ScriptedModel, simulate_llm_call

SIGNALS = {
    "sexual_content": "none",
    "graphic_violence": "none",
    "hateful_content": "none",
    "offensive_text": "none",
    "context": "ordinary",
    "confidence": "high",
    "prompt_injection": False,
    "uncertain": False,
}


def sample_image():
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="JPEG")
    return validate_image(output.getvalue())


def agent(model: ScriptedModel, backend: str = "gemini") -> VisionAgent:
    return VisionAgent(model, model_version=f"{backend}/test", backend=backend)


def run(coro):
    return asyncio.run(coro)


class MessageTests(unittest.TestCase):
    def test_gemini_gets_the_prompt_and_the_image_as_a_base64_part(self) -> None:
        model = ScriptedModel(replies=[SIGNALS])

        observation = run(agent(model).moderate(sample_image()))

        system, human = model.seen[0]
        self.assertEqual(system.type, "system")
        self.assertIn("marketplace de energia solar", system.content)
        image_part = human.content[1]
        self.assertEqual(image_part["type"], "image")
        self.assertEqual(image_part["mime_type"], "image/jpeg")
        self.assertEqual(observation.assessment.confidence, "high")
        self.assertFalse(observation.safety_flagged)

    def test_groq_gets_the_image_as_a_data_url(self) -> None:
        model = ScriptedModel(replies=[SIGNALS])

        run(agent(model, "groq").moderate(sample_image()))

        part = model.seen[0][1].content[1]
        self.assertEqual(part["type"], "image_url")
        self.assertTrue(part["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_edited_prompt_replaces_the_default(self) -> None:
        model = ScriptedModel(replies=[SIGNALS])

        run(agent(model).moderate(sample_image(), prompt="PROMPT EDITADO"))

        self.assertEqual(model.seen[0][0].content, "PROMPT EDITADO")


class CaptionTests(unittest.TestCase):
    def caption(self, model: ScriptedModel, **kwargs):
        return run(
            agent(model).caption(
                sample_image(),
                purpose="product",
                title="Painel",
                max_alt_chars=kwargs.pop("max_alt_chars", 12),
                **kwargs,
            )
        )

    def test_short_caption_retries_when_the_first_exceeds_the_limit(self) -> None:
        model = ScriptedModel(
            replies=[
                {"alt_text": "Painéis solares sobre telhado", "uncertain": False},
                {"alt_text": "Painel sol", "uncertain": False},
            ]
        )

        suggestion = self.caption(model)

        self.assertEqual(suggestion.alt_text, "Painel sol")
        self.assertEqual(len(model.seen), 2)
        self.assertIn("excedeu o limite", model.seen[1][0].content)

    def test_custom_prompt_is_not_reused_by_the_retry(self) -> None:
        model = ScriptedModel(
            replies=[
                {"alt_text": "Texto muito comprido demais", "uncertain": False},
                {"alt_text": "Painel", "uncertain": False},
            ]
        )

        self.caption(model, prompt="PROMPT EDITADO")

        self.assertEqual(model.seen[0][0].content, "PROMPT EDITADO")
        self.assertNotEqual(model.seen[1][0].content, "PROMPT EDITADO")

    def test_caption_inside_the_limit_is_one_call(self) -> None:
        model = ScriptedModel(replies=[{"alt_text": "Painel", "uncertain": False}])

        self.caption(model)

        self.assertEqual(len(model.seen), 1)

    def test_safety_flag_on_the_caption_is_blocked(self) -> None:
        model = ScriptedModel(
            replies=[
                (
                    {"alt_text": "Painel", "uncertain": False},
                    {
                        "finish_reason": "STOP",
                        "safety_ratings": [{"probability": "HIGH"}],
                    },
                )
            ]
        )

        with self.assertRaises(ProviderBlocked):
            self.caption(model)


class SafetyAndInvalidOutputTests(unittest.TestCase):
    def moderate(self, reply):
        return run(agent(ScriptedModel(replies=[reply])).moderate(sample_image()))

    def test_prompt_block_never_looks_safe(self) -> None:
        with self.assertRaises(ProviderBlocked):
            self.moderate((SIGNALS, {"prompt_feedback": {"block_reason": "SAFETY"}}))

    def test_safety_finish_is_blocked(self) -> None:
        with self.assertRaises(ProviderBlocked):
            self.moderate((SIGNALS, {"finish_reason": "SAFETY"}))

    def test_incomplete_answer_is_blocked(self) -> None:
        with self.assertRaises(ProviderBlocked):
            self.moderate((SIGNALS, {"finish_reason": "MAX_TOKENS"}))

    def test_medium_or_high_rating_flags_the_signals(self) -> None:
        observation = self.moderate(
            (
                SIGNALS,
                {
                    "finish_reason": "STOP",
                    "safety_ratings": [{"probability": "MEDIUM"}],
                },
            )
        )

        self.assertTrue(observation.safety_flagged)

    def test_invalid_structured_output_is_an_error(self) -> None:
        with self.assertRaises(ProviderError) as raised:
            self.moderate({})
        self.assertNotIsInstance(raised.exception, ProviderBlocked)

    def test_exception_mentioning_safety_is_blocked(self) -> None:
        with self.assertRaises(ProviderBlocked):
            self.moderate(RuntimeError("blocked by safety filter"))

    def test_http_failure_keeps_only_the_status_code(self) -> None:
        failure = RuntimeError("segredo-do-provedor")
        failure.status_code = 429

        with self.assertRaises(ProviderError) as raised:
            self.moderate(failure)

        self.assertEqual(raised.exception.status_code, 429)
        self.assertNotIn("segredo-do-provedor", str(raised.exception))


class CallbackTests(unittest.TestCase):
    def test_callbacks_record_prompt_model_and_tokens(self) -> None:
        capture = LlmCallCapture()

        run(simulate_llm_call({"callbacks": [capture]}, "PROMPT"))

        (record,) = capture.take("test/model")
        self.assertEqual(record.prompt, "PROMPT")
        self.assertEqual(record.model, "test/model")
        self.assertEqual((record.tokens_in, record.tokens_out), (10, 5))
        self.assertIsInstance(capture, AsyncCallbackHandler)

    def test_take_empties_the_list(self) -> None:
        capture = LlmCallCapture()
        run(simulate_llm_call({"callbacks": [capture]}, "PROMPT"))

        capture.take("a")

        self.assertEqual(capture.take("b"), [])


class KeysTests(unittest.TestCase):
    def test_legacy_names_still_work(self) -> None:
        env = llm_environment(
            {"GEMINI_AI_ACCESSIBILITY_KEY": "g", "GROQ_AI_ACCESSIBILITY_KEY": "q"}
        )

        self.assertEqual(env["GEMINI_API_KEY"], "g")
        self.assertEqual(env["GROQ_API_KEY"], "q")

    def test_shared_names_win_over_legacy(self) -> None:
        env = llm_environment({"GEMINI_API_KEY": "novo", "GOOGLE_API_KEY": "velho"})

        self.assertEqual(env["GEMINI_API_KEY"], "novo")

    def test_order_follows_the_configuration(self) -> None:
        both = {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"}

        self.assertEqual(vision_providers(both), ["gemini", "groq"])
        self.assertEqual(
            vision_providers({**both, "LLM_PROVIDER_ORDER": "groq,gemini"}),
            ["groq", "gemini"],
        )
        self.assertEqual(vision_providers({"GROQ_API_KEY": "q"}), ["groq"])
        self.assertEqual(vision_providers({}), [])

    def test_openai_never_analyzes_images_here(self) -> None:
        self.assertEqual(vision_providers({"OPENAI_API_KEY": "o"}), [])

    def test_agents_follow_the_providers_with_a_key(self) -> None:
        settings = Settings(llm_env={"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"})

        agents = build_vision_agents(settings)

        self.assertEqual(
            [item.model_version for item in agents],
            [f"gemini/{settings.gemini_model}", f"groq/{settings.groq_model}"],
        )

    def test_no_keys_no_agents(self) -> None:
        self.assertEqual(build_vision_agents(Settings(llm_env={})), [])


if __name__ == "__main__":
    unittest.main()
