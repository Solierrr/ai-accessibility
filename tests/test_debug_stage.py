import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from src.api.app import create_app
from src.core.config import Settings
from src.providers.capture import CallCapture
from src.providers.gemini import GeminiVisionProvider, ProviderError
from src.providers.groq import GroqVisionProvider
from src.providers.prompts import MODERATION_PROMPT
from src.services import debug_runs
from src.services.debug_runs import DebugRunStore, StoredRun
from src.services.trace import clean_prompt, mask_pii
from tests.test_analysis import valid_image
from tests.test_debug import (
    ADMIN,
    ADMIN_TOKEN,
    CAPTION,
    OUTRO,
    FakeProvider,
    fake_decode,
    make_settings,
)

ANALYZE = "/debug/images/analyze"
STAGE = "/debug/images/stage"


class Client:
    def __init__(self, settings=None, provider=None, fallback=None, decode=fake_decode):
        self.app = create_app(
            settings or make_settings(), provider or FakeProvider(), fallback
        )
        self.decode = decode

    def _request(self, method, path, **kwargs):
        async def run() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                return await client.request(method, path, **kwargs)

        with patch("src.api.debug_access.decode_user_id", self.decode):
            return asyncio.run(run())

    def analyze(self, *, title="", token=ADMIN_TOKEN, content=None):
        return self._request(
            "POST",
            ANALYZE,
            data={"purpose": "product", "context_title": title},
            files={"image": ("p.png", content or valid_image(), "image/png")},
            headers={"Authorization": f"Bearer {token}"},
        )

    def stage(self, body, token=ADMIN_TOKEN):
        return self._request(
            "POST", STAGE, json=body, headers={"Authorization": f"Bearer {token}"}
        )


def body(run_id, node="caption", prompt="PROMPT NOVO", occurrence=0):
    return {
        "run_id": run_id,
        "node": node,
        "occurrence": occurrence,
        "stop_after_node": True,
        "prompts": [
            {
                "call": 0,
                "prompt": [
                    {"type": "system", "content": prompt},
                    {"type": "human", "content": "[imagem omitida]"},
                ],
            }
        ],
    }


class LlmCallsInTheTrace(unittest.TestCase):
    def test_moderation_and_caption_carry_prompt_model_and_tokens(self) -> None:
        debug = Client().analyze().json()["debug"]

        nodes = {n["node"]: n for n in debug["nodes"]}
        for stage in ("moderation", "caption"):
            (call,) = nodes[stage]["llm_calls"]
            self.assertEqual(call["model"], "test/fake")
            self.assertEqual(call["prompt"][0]["type"], "system")
            self.assertTrue(call["prompt"][0]["editable"])
            self.assertEqual(call["prompt"][1]["content"], "[imagem omitida]")
            self.assertFalse(call["prompt"][1]["editable"])
            self.assertTrue(call["prompt_complete"])
            self.assertEqual((call["tokens_in"], call["tokens_out"]), (10, 5))
        self.assertEqual(nodes["policy"]["llm_calls"], [])

    def test_completion_is_a_summary_without_the_caption_text(self) -> None:
        response = Client().analyze()

        nodes = {n["node"]: n for n in response.json()["debug"]["nodes"]}
        completion = json.loads(nodes["caption"]["llm_calls"][0]["completion"])
        self.assertEqual(completion["alt_text_chars"], len(CAPTION))
        self.assertNotIn(
            CAPTION, json.dumps(response.json()["debug"], ensure_ascii=False)
        )

    def test_title_with_pii_is_masked_and_blocks_editing(self) -> None:
        class Echo(FakeProvider):
            async def caption(
                self, image, *, purpose, title, max_alt_chars, prompt=None, capture=None
            ):
                if capture is not None:
                    capture.begin(f"Título: {title}", self.model_version).done()
                return await super().caption(
                    image, purpose=purpose, title=title, max_alt_chars=max_alt_chars
                )

        response = Client(provider=Echo()).analyze(title="Contato 123.456.789-09")

        nodes = {n["node"]: n for n in response.json()["debug"]["nodes"]}
        call = nodes["caption"]["llm_calls"][0]
        self.assertIn("[CPF]", call["prompt"][0]["content"])
        self.assertNotIn("123.456.789-09", json.dumps(response.json()))
        self.assertFalse(call["prompt_complete"])
        self.assertFalse(call["prompt"][0]["editable"])

    def test_failed_attempt_has_error_without_provider_message(self) -> None:
        primary = FakeProvider(
            "test/primary", moderation_error=ProviderError("segredo", status_code=429)
        )

        response = Client(provider=primary, fallback=FakeProvider("test/fb")).analyze()

        first = response.json()["debug"]["nodes"][1]
        self.assertEqual(first["llm_calls"][0]["error"], "ProviderError (HTTP 429)")
        self.assertIsNone(first["llm_calls"][0]["completion"])
        self.assertNotIn("segredo", json.dumps(response.json()))

    def test_run_id_and_turn_come_with_a_valid_image_only(self) -> None:
        client = Client()

        good = client.analyze().json()["debug"]
        bad = client.analyze(content=b"nao e imagem").json()["debug"]

        self.assertEqual(len(good["run_id"]), 36)
        self.assertEqual(good["turn"], 0)
        self.assertNotIn("run_id", bad)

    def test_normal_route_calls_the_provider_without_capture(self) -> None:
        class Strict(FakeProvider):
            async def moderate(self, image):
                return await FakeProvider.moderate(self, image)

        async def run():
            app = create_app(make_settings(), Strict(), None)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.post(
                    "/v1/images/analyze",
                    data={"purpose": "product"},
                    files={"image": ("p.png", valid_image(), "image/png")},
                    headers={"Authorization": "Bearer test-secret"},
                )

        self.assertEqual(asyncio.run(run()).status_code, 200)


class StageReplay(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = FakeProvider()
        self.client = Client(provider=self.provider)
        self.run_id = self.client.analyze().json()["debug"]["run_id"]

    def test_caption_runs_alone_with_the_edited_prompt(self) -> None:
        self.provider.caption_calls = 0

        response = self.client.stage(body(self.run_id))

        debug = response.json()["debug"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual([n["node"] for n in debug["nodes"]], ["caption"])
        self.assertEqual(self.provider.last_prompt, "PROMPT NOVO")
        self.assertEqual(self.provider.caption_calls, 1)
        call = debug["nodes"][0]["llm_calls"][0]
        self.assertEqual(call["prompt"][0]["content"], "PROMPT NOVO")
        self.assertEqual(
            debug["custom"],
            {
                "run_id": self.run_id,
                "node": "caption",
                "turn": 0,
                "occurrence": 0,
                "stopped": True,
                "overrides_applied": 1,
                "next": ["caption_validation"],
            },
        )

    def test_moderation_runs_alone_and_points_to_the_policy(self) -> None:
        response = self.client.stage(body(self.run_id, "moderation"))

        debug = response.json()["debug"]
        self.assertEqual([n["node"] for n in debug["nodes"]], ["moderation"])
        self.assertEqual(debug["custom"]["next"], ["policy"])
        self.assertEqual(self.provider.last_prompt, "PROMPT NOVO")

    def test_unchanged_prompt_counts_as_zero_applied(self) -> None:
        response = self.client.stage(body(self.run_id, "moderation", MODERATION_PROMPT))

        self.assertEqual(response.json()["debug"]["custom"]["overrides_applied"], 0)

    def test_response_never_carries_caption_text_or_image(self) -> None:
        raw = json.dumps(
            self.client.stage(body(self.run_id)).json(), ensure_ascii=False
        )

        self.assertNotIn(CAPTION, raw)
        self.assertNotIn("base64", raw)

    def test_stages_without_a_model_call_are_blocked_with_a_reason(self) -> None:
        for node in ("policy", "image_validation", "caption_validation"):
            with self.subTest(node=node):
                response = self.client.stage(body(self.run_id, node))
                self.assertEqual(response.status_code, 422)
                self.assertIn("não chama um modelo", response.json()["detail"])

    def test_unknown_run_is_404(self) -> None:
        response = self.client.stage(body("11111111-1111-4111-8111-111111111111"))

        self.assertEqual(response.status_code, 404)
        self.assertIn("expirou", response.json()["detail"])

    def test_another_admin_cannot_use_the_run(self) -> None:
        settings = make_settings(debug_admin_subs=f"{ADMIN},{OUTRO}")
        decode = lambda token, **_: ADMIN if token == ADMIN_TOKEN else OUTRO
        client = Client(settings, decode=decode)
        run_id = client.analyze().json()["debug"]["run_id"]

        response = client.stage(body(run_id), token="outro-admin")

        self.assertEqual(response.status_code, 404)

    def test_occurrence_out_of_range_is_404(self) -> None:
        response = self.client.stage(body(self.run_id, occurrence=3))

        self.assertEqual(response.status_code, 404)

    def test_invalid_bodies_are_400(self) -> None:
        for change in (
            {"run_id": "x"},
            {"node": "Nó Inválido"},
            {"occurrence": -1},
        ):
            with self.subTest(change=change):
                response = self.client.stage({**body(self.run_id), **change})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["errorCode"], "VALIDATION_ERROR")

    def test_oversized_body_is_413(self) -> None:
        response = self.client._request(
            "POST",
            STAGE,
            content=b"0" * (9 * 1024 * 1024),
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(response.status_code, 413)

    def test_access_rules_apply(self) -> None:
        self.assertEqual(
            self.client.stage(body(self.run_id), token="x").status_code, 401
        )
        hidden = Client(make_settings(debug_admin_subs=""))
        self.assertEqual(hidden.stage(body(self.run_id)).status_code, 404)
        internal = self.client.stage(body(self.run_id), token="test-secret")
        self.assertEqual(internal.status_code, 401)

    def test_route_is_not_in_the_openapi(self) -> None:
        self.assertFalse(
            any(
                path.startswith("/debug") for path in self.client.app.openapi()["paths"]
            )
        )

    def test_provider_failure_is_a_node_with_error(self) -> None:
        self.provider.fail_moderation = True

        response = self.client.stage(body(self.run_id, "moderation"))

        node = response.json()["debug"]["nodes"][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(node["status"], "error")
        self.assertNotIn("Falha de teste", json.dumps(response.json()))

    def test_replay_uses_the_provider_of_that_attempt(self) -> None:
        primary = FakeProvider(
            "test/primary", moderation_error=ProviderError("falha", status_code=503)
        )
        fallback = FakeProvider("test/fallback")
        client = Client(provider=primary, fallback=fallback)
        run_id = client.analyze().json()["debug"]["run_id"]
        primary.moderation_error = None

        first = client.stage(body(run_id, "moderation", occurrence=0)).json()
        second = client.stage(body(run_id, "moderation", occurrence=1)).json()

        self.assertEqual(
            first["debug"]["nodes"][0]["output"]["provider"], "test/primary"
        )
        self.assertEqual(
            second["debug"]["nodes"][0]["output"]["provider"], "test/fallback"
        )

    def test_stage_logs_the_audit_line_without_the_prompt(self) -> None:
        with self.assertLogs("src.api.app", level="INFO") as logs:
            self.client.stage(body(self.run_id))

        line = next(m for m in logs.output if "etapa" in m)
        self.assertIn(ADMIN, line)
        self.assertIn("caption", line)
        self.assertNotIn("PROMPT NOVO", line)


class RunStore(unittest.TestCase):
    @staticmethod
    def make_run(sub="a", size=10):
        return StoredRun(sub, b"x" * size, "product", 100, "", {})

    def test_oldest_runs_of_the_same_admin_are_dropped(self) -> None:
        store = DebugRunStore()
        with patch.object(debug_runs, "MAX_RUNS_PER_SUB", 2):
            ids = [store.add(self.make_run()) for _ in range(3)]

        self.assertIsNone(store.get("a", ids[0]))
        self.assertIsNotNone(store.get("a", ids[1]))
        self.assertIsNotNone(store.get("a", ids[2]))

    def test_other_admins_do_not_count_against_the_limit(self) -> None:
        store = DebugRunStore()
        with patch.object(debug_runs, "MAX_RUNS_PER_SUB", 1):
            mine = store.add(self.make_run("a"))
            store.add(self.make_run("b"))

        self.assertIsNotNone(store.get("a", mine))

    def test_byte_cap_drops_the_oldest_but_keeps_the_new_run(self) -> None:
        store = DebugRunStore()
        with patch.object(debug_runs, "MAX_BYTES", 100):
            first = store.add(self.make_run(size=60))
            second = store.add(self.make_run(size=60))
            giant = store.add(self.make_run(size=500))

        self.assertIsNone(store.get("a", first))
        self.assertIsNone(store.get("a", second))
        self.assertIsNotNone(store.get("a", giant))

    def test_expired_runs_disappear(self) -> None:
        store = DebugRunStore()
        run_id = store.add(self.make_run())
        with patch.object(debug_runs, "MAX_AGE_SECONDS", -1):
            self.assertIsNone(store.get("a", run_id))

    def test_run_belongs_to_its_admin(self) -> None:
        store = DebugRunStore()
        run_id = store.add(self.make_run("a"))

        self.assertIsNone(store.get("b", run_id))


class Masking(unittest.TestCase):
    def test_masks_documents_emails_and_phones(self) -> None:
        text = "CPF 123.456.789-09, CNPJ 12.345.678/0001-90, a@b.com, (11) 91234-5678"

        masked = mask_pii(text)

        for leaked in ("123.456.789-09", "12.345.678/0001-90", "a@b.com", "91234-5678"):
            self.assertNotIn(leaked, masked)

    def test_plain_prompt_is_complete(self) -> None:
        self.assertEqual(
            clean_prompt("Painel 550W em telhado"), ("Painel 550W em telhado", True)
        )

    def test_huge_prompt_comes_whole_and_editable(self) -> None:
        text, complete = clean_prompt("a" * 500000)

        self.assertTrue(complete)
        self.assertEqual(len(text), 500000)


def _handler(payload):
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handle


class ProviderCapture(unittest.TestCase):
    @staticmethod
    def gemini_payload(text, usage=None):
        payload = {
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": text}]}}
            ]
        }
        if usage:
            payload["usageMetadata"] = usage
        return payload

    def image(self):
        from tests.test_gemini_provider import sample_image

        return sample_image()

    def test_gemini_records_prompt_model_and_tokens(self) -> None:
        payload = self.gemini_payload(
            json.dumps({"alt_text": "Painel", "uncertain": False}),
            {"promptTokenCount": 120, "candidatesTokenCount": 7},
        )

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(_handler(payload))
            ) as client:
                provider = GeminiVisionProvider(Settings("k", "t"), client)
                capture = CallCapture()
                await provider.caption(
                    self.image(),
                    purpose="product",
                    title="",
                    max_alt_chars=100,
                    prompt="MEU PROMPT",
                    capture=capture,
                )
                return capture

        (record,) = asyncio.run(run()).calls
        self.assertEqual(record.prompt, "MEU PROMPT")
        self.assertTrue(record.model.startswith("gemini/"))
        self.assertEqual((record.tokens_in, record.tokens_out), (120, 7))
        self.assertIsNone(record.error)

    def test_custom_prompt_is_not_reused_by_the_retry(self) -> None:
        sent = []

        def handle(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content)["contents"][0]["parts"][0]["text"])
            text = "Muito longa para o limite" if len(sent) == 1 else "Curta"
            return httpx.Response(
                200,
                json=self.gemini_payload(
                    json.dumps({"alt_text": text, "uncertain": False})
                ),
            )

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle)
            ) as client:
                provider = GeminiVisionProvider(Settings("k", "t"), client)
                capture = CallCapture()
                await provider.caption(
                    self.image(),
                    purpose="product",
                    title="",
                    max_alt_chars=10,
                    prompt="MEU PROMPT",
                    capture=capture,
                )
                return capture

        capture = asyncio.run(run())
        self.assertEqual(sent[0], "MEU PROMPT")
        self.assertIn("A resposta anterior excedeu", sent[1])
        self.assertEqual(len(capture.calls), 2)

    def test_failure_keeps_only_type_and_status(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="segredo do provedor")

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle)
            ) as client:
                provider = GeminiVisionProvider(Settings("k", "t"), client)
                capture = CallCapture()
                image = self.image()
                with self.assertRaises(ProviderError):
                    await provider.moderate(image, capture=capture)
                return capture

        (record,) = asyncio.run(run()).calls
        self.assertEqual(record.error, "ProviderError (HTTP 429)")
        self.assertNotIn("segredo", repr(record))

    def test_groq_records_tokens_and_uses_the_given_prompt(self) -> None:
        sent = []
        assessment = {
            "sexual_content": "none",
            "graphic_violence": "none",
            "hateful_content": "none",
            "offensive_text": "none",
            "context": "ordinary",
            "confidence": "high",
            "prompt_injection": False,
            "uncertain": False,
        }

        def handle(request: httpx.Request) -> httpx.Response:
            sent.append(
                json.loads(request.content)["messages"][0]["content"][0]["text"]
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(assessment)},
                        }
                    ],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 9},
                },
            )

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle)
            ) as client:
                provider = GroqVisionProvider(
                    Settings(None, "t", groq_api_key="g"), client
                )
                capture = CallCapture()
                await provider.moderate(self.image(), prompt="OUTRO", capture=capture)
                return capture

        (record,) = asyncio.run(run()).calls
        self.assertEqual(sent, ["OUTRO"])
        self.assertEqual((record.tokens_in, record.tokens_out), (50, 9))

    def test_without_capture_nothing_is_recorded(self) -> None:
        payload = self.gemini_payload(
            json.dumps({"alt_text": "Painel", "uncertain": False})
        )

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(_handler(payload))
            ) as client:
                provider = GeminiVisionProvider(Settings("k", "t"), client)
                return await provider.caption(
                    self.image(), purpose="product", title="", max_alt_chars=100
                )

        self.assertEqual(asyncio.run(run()).alt_text, "Painel")


if __name__ == "__main__":
    unittest.main()
