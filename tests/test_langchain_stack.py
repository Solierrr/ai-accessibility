"""Pilha real (ai_lib + LangChain + LangGraph), só com a API da Groq simulada por HTTP."""

import asyncio
import json
import unittest

import httpx
import respx

from src.api.app import create_app
from tests.test_analysis import valid_image
from tests.test_debug import SERVICE_KEY, make_settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
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
CAPTION = {"alt_text": "Painel solar azul.", "uncertain": False}


def completion(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "c",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
    )


class Groq:
    """Resposta de cada chamada ao modelo, na ordem; guarda o que o serviço enviou."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answers.pop(0)

    def body(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


def analyze(groq: Groq, env: dict[str, str]) -> httpx.Response:
    async def go() -> httpx.Response:
        app = create_app(make_settings(llm_env=env))
        with respx.mock(assert_all_called=False) as mock:
            mock.post(GROQ_URL).mock(side_effect=groq)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.post(
                    "/v1/images/analyze",
                    data={"purpose": "product"},
                    files={"image": ("p.png", valid_image(), "image/png")},
                    headers={"X-Service-Key": SERVICE_KEY},
                )

    return asyncio.run(go())


class RealStackTests(unittest.TestCase):
    def test_two_model_calls_approve_and_send_the_image_with_a_json_schema(
        self,
    ) -> None:
        groq = Groq(completion(SIGNALS), completion(CAPTION))

        response = analyze(groq, {"GROQ_API_KEY": "gsk_one"})

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["decision"], "approved")
        self.assertEqual(body["alt_text"], "Painel solar azul.")
        self.assertTrue(body["model_version"].startswith("groq/"))
        self.assertEqual(len(groq.requests), 2)
        first = groq.body(0)
        self.assertEqual([m["role"] for m in first["messages"]], ["system", "user"])
        self.assertTrue(
            any(
                part.get("type") == "image_url"
                for part in first["messages"][1]["content"]
            )
        )
        self.assertEqual(first["response_format"]["type"], "json_schema")
        self.assertEqual(groq.requests[0].headers["authorization"], "Bearer gsk_one")

    def test_rate_limited_key_rotates_to_the_next_one(self) -> None:
        limited = httpx.Response(429, json={"error": {"message": "rate limit"}})
        groq = Groq(limited, completion(SIGNALS), completion(CAPTION))

        response = analyze(
            groq, {"GROQ_API_KEY": "gsk_one", "GROQ_API_KEY_2": "gsk_two"}
        )

        self.assertEqual(response.json()["decision"], "approved")
        used = [r.headers["authorization"] for r in groq.requests]
        self.assertEqual(used[0], "Bearer gsk_one")
        self.assertEqual(used[1], "Bearer gsk_two")

    def test_every_key_failing_asks_for_review_without_leaking_the_error(self) -> None:
        broken = httpx.Response(500, json={"error": {"message": "segredo-do-provedor"}})
        groq = Groq(*[broken] * 6)

        response = analyze(groq, {"GROQ_API_KEY": "gsk_one"})

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["decision"], "review_required")
        self.assertEqual(body["reason_codes"], ["PROVIDER_UNAVAILABLE"])
        self.assertNotIn("segredo-do-provedor", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
