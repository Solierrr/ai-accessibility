"""Decisão, falhas e contrato HTTP sem chamadas externas."""

import asyncio
import unittest
from io import BytesIO

import httpx
from PIL import Image

from src.api.app import create_app
from src.core.config import Settings
from src.moderation.models import CaptionSuggestion, VisionAssessment
from src.moderation.policy import Decision, decide
from src.providers.gemini import ModerationObservation, ProviderError
from src.services.analysis import analyze_image


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

    async def moderate(self, image):
        if self.fail_moderation:
            raise ProviderError("Falha de teste")
        return ModerationObservation(self.visual, False)

    async def caption(self, image, *, purpose, title, max_alt_chars):
        self.caption_calls += 1
        self.last_max_alt_chars = max_alt_chars
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
                google_api_key="test-key", internal_api_token="test-secret"
            )
            app = create_app(settings, FakeProvider())
            operation = app.openapi()["paths"]["/v1/images/analyze"]["post"]
            self.assertTrue(operation["security"])
            schema_ref = operation["requestBody"]["content"]["multipart/form-data"][
                "schema"
            ]["$ref"]
            body_schema = app.openapi()["components"]["schemas"][
                schema_ref.split("/")[-1]
            ]
            self.assertNotIn("locale", body_schema["properties"])
            self.assertIn("max_alt_chars", body_schema["properties"])
            self.assertNotIn(
                "authorization", [item["name"] for item in operation.get("parameters", [])]
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

                authorized = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files=files,
                    headers={"Authorization": "Bearer test-secret"},
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
                    headers={"Authorization": "Bearer test-secret"},
                )
                self.assertEqual(larger_limit.status_code, 200)

                invalid_limit = await client.post(
                    "/v1/images/analyze",
                    data={**payload, "max_alt_chars": "0"},
                    files=files,
                    headers={"Authorization": "Bearer test-secret"},
                )
                self.assertEqual(invalid_limit.status_code, 422)

                invalid = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files={"image": ("fake.jpg", b"not an image", "image/jpeg")},
                    headers={"Authorization": "Bearer test-secret"},
                )
                self.assertEqual(invalid.json()["decision"], "rejected")
                self.assertEqual(invalid.json()["reason_codes"], ["INVALID_IMAGE"])

                too_large = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files={
                        "image": ("huge.jpg", b"x" * (9 * 1024 * 1024), "image/jpeg")
                    },
                    headers={"Authorization": "Bearer test-secret"},
                )
                self.assertEqual(too_large.status_code, 413)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
