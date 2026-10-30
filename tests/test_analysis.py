"""Decisão, falhas e contrato HTTP sem chamadas externas."""

import asyncio
import unittest
from io import BytesIO

import httpx
from PIL import Image

from src.api.app import create_app
from src.api.errors import ApiError
from src.api.service_auth import resolve_caller
from src.core.config import Settings
from src.moderation.models import CaptionSuggestion, VisionAssessment
from src.moderation.policy import Decision, decide
from src.providers.gemini import ModerationObservation, ProviderError
from src.services.analysis import analyze_image
from src.version import VERSION

SERVICE_KEY = "k" * 32
SERVICE_HEADERS = {"X-Service-Key": SERVICE_KEY}


def valid_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(output, format="PNG")
    return output.getvalue()


def assessment(**overrides: object) -> VisionAssessment:
    data = {
        "sexual_content": "none",
        "graphic_violence": "none",
        "hateful_content": "none",
        "offensive_text": "none",
        "context": "ordinary",
        "confidence": "high",
        "prompt_injection": False,
        "uncertain": False,
    }
    data.update(overrides)
    return VisionAssessment.model_validate(data)


class FakeProvider:
    model_version = "test/fake"

    def __init__(
        self,
        *,
        visual: VisionAssessment | None = None,
        caption: str = "Painel solar azul sobre uma mesa.",
    ):
        self.visual = visual or assessment()
        self.caption_text = caption
        self.caption_calls = 0
        self.fail_moderation = False

    def _record(self, prompt, capture, default):
        self.last_prompt = prompt
        if capture is not None:
            record = capture.begin(prompt or default, self.model_version)
            record.done(tokens_in=10, tokens_out=5)

    async def moderate(self, image, *, prompt=None, capture=None):
        if self.fail_moderation:
            raise ProviderError("Falha de teste")
        self._record(prompt, capture, "MODERATION-PROMPT")
        return ModerationObservation(self.visual, False)

    async def caption(
        self, image, *, purpose, title, max_alt_chars, prompt=None, capture=None
    ):
        self.caption_calls += 1
        self.last_max_alt_chars = max_alt_chars
        self._record(prompt, capture, "CAPTION-PROMPT")
        return CaptionSuggestion(alt_text=self.caption_text, uncertain=False)


class PolicyTests(unittest.TestCase):
    def test_severe_risk_is_rejected_without_caption(self) -> None:
        provider = FakeProvider(visual=assessment(graphic_violence="explicit"))
        result = asyncio.run(
            analyze_image(
                valid_image(),
                provider=provider,
                request_id="r1",
                purpose="product",
            )
        )
        self.assertEqual(result.decision, Decision.REJECTED)
        self.assertEqual(result.reason_codes, ["GRAPHIC_VIOLENCE"])
        self.assertEqual(result.risk_categories, ["GRAPHIC_VIOLENCE"])
        self.assertIsNone(result.alt_text)
        self.assertEqual(provider.caption_calls, 0)

    def test_documentary_and_ambiguous_cases_require_review(self) -> None:
        self.assertEqual(
            decide(
                assessment(hateful_content="explicit", context="documentary")
            ).decision,
            Decision.REVIEW_REQUIRED,
        )
        self.assertEqual(
            decide(assessment(), safety_flagged=True).decision, Decision.REVIEW_REQUIRED
        )
        self.assertEqual(
            decide(assessment(confidence="low")).decision, Decision.REVIEW_REQUIRED
        )

    def test_safe_image_needs_valid_caption(self) -> None:
        provider = FakeProvider()
        result = asyncio.run(
            analyze_image(
                valid_image(),
                provider=provider,
                request_id="r2",
                purpose="product",
            )
        )
        self.assertEqual(result.decision, Decision.APPROVED)
        self.assertEqual(result.alt_text, provider.caption_text)

        provider.caption_text = ""
        invalid = asyncio.run(
            analyze_image(
                valid_image(),
                provider=provider,
                request_id="r3",
                purpose="product",
            )
        )
        self.assertEqual(invalid.decision, Decision.REVIEW_REQUIRED)
        self.assertEqual(invalid.reason_codes, ["CAPTION_INVALID"])

        provider.caption_text = "A" * 151
        too_long = asyncio.run(
            analyze_image(
                valid_image(),
                provider=provider,
                request_id="r5",
                purpose="product",
            )
        )
        self.assertEqual(too_long.decision, Decision.REVIEW_REQUIRED)
        self.assertEqual(too_long.reason_codes, ["CAPTION_INVALID"])
        self.assertIsNone(too_long.alt_text)

        provider.caption_text = "A" * 150
        boundary = asyncio.run(
            analyze_image(
                valid_image(),
                provider=provider,
                request_id="r6",
                purpose="product",
            )
        )
        self.assertEqual(boundary.decision, Decision.APPROVED)
        self.assertEqual(len(boundary.alt_text or ""), 150)

        provider.caption_text = "A" * 81
        custom_limit = asyncio.run(
            analyze_image(
                valid_image(),
                provider=provider,
                request_id="r7",
                purpose="product",
                max_alt_chars=80,
            )
        )
        self.assertEqual(provider.last_max_alt_chars, 80)
        self.assertEqual(custom_limit.reason_codes, ["CAPTION_INVALID"])

    def test_provider_failure_never_approves(self) -> None:
        provider = FakeProvider()
        provider.fail_moderation = True
        result = asyncio.run(
            analyze_image(
                valid_image(),
                provider=provider,
                request_id="r4",
                purpose="product",
            )
        )
        self.assertEqual(result.decision, Decision.REVIEW_REQUIRED)
        self.assertEqual(result.reason_codes, ["PROVIDER_UNAVAILABLE"])


class ApiTests(unittest.TestCase):
    def test_authentication_and_analysis_contract(self) -> None:
        async def run() -> None:
            settings = Settings(
                google_api_key="test-key", service_keys={"api-core": SERVICE_KEY}
            )
            app = create_app(settings, FakeProvider())
            operation = app.openapi()["paths"]["/v1/images/analyze"]["post"]
            self.assertEqual(operation["security"], [{"serviceKey": []}])
            schema_ref = operation["requestBody"]["content"]["multipart/form-data"][
                "schema"
            ]["$ref"]
            body_schema = app.openapi()["components"]["schemas"][
                schema_ref.split("/")[-1]
            ]
            self.assertNotIn("locale", body_schema["properties"])
            self.assertIn("max_alt_chars", body_schema["properties"])
            self.assertNotIn(
                "authorization",
                [item["name"] for item in operation.get("parameters", [])],
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {
                    "purpose": "product",
                    "request_id": "upload-1",
                }
                files = {"image": ("panel.png", valid_image(), "image/png")}
                unauthorized = await client.post(
                    "/v1/images/analyze", data=payload, files=files
                )
                self.assertEqual(unauthorized.status_code, 401)
                self.assertEqual(
                    unauthorized.headers["content-type"], "application/problem+json"
                )
                self.assertEqual(unauthorized.json()["errorCode"], "UNAUTHENTICATED")
                self.assertEqual(unauthorized.json()["instance"], "/v1/images/analyze")

                old_token = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files=files,
                    headers={"Authorization": f"Bearer {SERVICE_KEY}"},
                )
                self.assertEqual(old_token.status_code, 401)

                wrong_key = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files=files,
                    headers={"X-Service-Key": "x" * 40},
                )
                self.assertEqual(wrong_key.status_code, 401)
                self.assertEqual(
                    wrong_key.json()["detail"], "Chave de serviço ausente ou inválida."
                )

                authorized = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files=files,
                    headers=SERVICE_HEADERS,
                )
                self.assertEqual(authorized.status_code, 200)
                body = authorized.json()
                self.assertEqual(body["decision"], "approved")
                self.assertEqual(body["request_id"], "upload-1")
                self.assertTrue(body["alt_text"])
                self.assertEqual(body["risk_categories"], [])
                self.assertNotIn("categories", body)
                self.assertNotIn("locale", body)
                self.assertNotIn("content", body)

                larger_limit = await client.post(
                    "/v1/images/analyze",
                    data={**payload, "max_alt_chars": "151"},
                    files=files,
                    headers=SERVICE_HEADERS,
                )
                self.assertEqual(larger_limit.status_code, 200)

                invalid_limit = await client.post(
                    "/v1/images/analyze",
                    data={**payload, "max_alt_chars": "0"},
                    files=files,
                    headers=SERVICE_HEADERS,
                )
                self.assertEqual(invalid_limit.status_code, 422)
                problem = invalid_limit.json()
                self.assertEqual(problem["type"], "about:blank")
                self.assertEqual(problem["title"], "VALIDATION_FAILED")
                self.assertEqual(problem["errors"][0]["field"], "max_alt_chars")
                self.assertIn("timestamp", problem)

                invalid = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files={"image": ("fake.jpg", b"not an image", "image/jpeg")},
                    headers=SERVICE_HEADERS,
                )
                self.assertEqual(invalid.json()["decision"], "rejected")
                self.assertEqual(invalid.json()["reason_codes"], ["INVALID_IMAGE"])

                too_large = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files={
                        "image": ("huge.jpg", b"x" * (9 * 1024 * 1024), "image/jpeg")
                    },
                    headers=SERVICE_HEADERS,
                )
                self.assertEqual(too_large.status_code, 413)
                self.assertEqual(too_large.json()["errorCode"], "PAYLOAD_TOO_LARGE")

                missing = await client.get("/v1/unknown")
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(missing.json()["errorCode"], "NOT_FOUND")

        asyncio.run(run())


class OpenApiTests(unittest.TestCase):
    def test_contract_follows_the_standard(self) -> None:
        contract = create_app(Settings(google_api_key="test-key")).openapi()
        self.assertEqual(contract["info"]["title"], "Solaria — ai-accessibility")
        self.assertEqual(contract["info"]["version"], VERSION)
        self.assertEqual(contract["servers"][0]["description"], "QA")
        self.assertEqual(contract["security"], [{"serviceKey": []}])
        self.assertIn("ProblemDetail", contract["components"]["schemas"])
        self.assertNotIn("HTTPValidationError", contract["components"]["schemas"])
        analyze = contract["paths"]["/v1/images/analyze"]["post"]
        self.assertEqual(analyze["operationId"], "analyzeImage")
        for code in ("401", "413", "422", "500", "503"):
            self.assertIn(
                "application/problem+json", analyze["responses"][code]["content"]
            )
        self.assertEqual(contract["paths"]["/health"]["get"]["security"], [])
        ready = contract["paths"]["/ready"]["get"]["responses"]["503"]
        self.assertIn("application/json", ready["content"])

    def test_docs_can_be_disabled(self) -> None:
        async def run() -> tuple[int, int]:
            app = create_app(Settings(google_api_key="test-key", docs_enabled=False))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                docs = await client.get("/docs")
                contract = await client.get("/openapi.json")
                return docs.status_code, contract.status_code

        self.assertEqual(asyncio.run(run()), (404, 404))


class HealthTests(unittest.TestCase):
    def test_health_and_readiness(self) -> None:
        async def check(settings: Settings) -> tuple[httpx.Response, httpx.Response]:
            app = create_app(settings, FakeProvider())
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.get("/health"), await client.get("/ready")

        health, ready = asyncio.run(check(Settings(google_api_key="test-key")))
        self.assertEqual(health.json(), {"status": "UP", "version": VERSION})
        self.assertEqual((ready.status_code, ready.json()), (200, {"status": "UP"}))

        health, ready = asyncio.run(check(Settings(google_api_key=None)))
        self.assertEqual(health.status_code, 200)
        self.assertEqual((ready.status_code, ready.json()), (503, {"status": "DOWN"}))


class ServiceKeyTests(unittest.TestCase):
    def test_destination_without_keys_refuses_everything(self) -> None:
        with self.assertRaises(ApiError) as raised:
            resolve_caller(SERVICE_KEY, {}, ())
        self.assertEqual(raised.exception.status, 401)

    def test_caller_not_accepted_on_the_route_is_forbidden(self) -> None:
        with self.assertRaises(ApiError) as raised:
            resolve_caller(SERVICE_KEY, {"api-core": SERVICE_KEY}, ("api-auth",))
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.error_code, "FORBIDDEN")

    def test_accepted_caller_is_resolved(self) -> None:
        self.assertEqual(
            resolve_caller(SERVICE_KEY, {"api-core": SERVICE_KEY}, ("api-core",)),
            "api-core",
        )

    def test_short_key_is_refused_at_startup(self) -> None:
        with self.assertRaises(ValueError):
            Settings(google_api_key="test-key", service_keys={"api-core": "short"})


if __name__ == "__main__":
    unittest.main()
