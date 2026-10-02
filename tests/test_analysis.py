import asyncio
import unittest
from io import BytesIO

import httpx
from PIL import Image

from src.api.app import create_app
from src.api.auth import AuthKeysUnavailable, InvalidAccessToken
from src.core.config import Settings
from src.moderation.models import ImageAnalysisSuggestion, VisionAssessment
from src.moderation.policy import Decision, decide
from src.providers.gemini import ImageObservation, ProviderError
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
        is_safe: bool = True,
        purpose_evidence: str = "photovoltaic_product",
        person_presentation: str = "not_applicable",
        scene_context: str = "neutral",
        solar_visual_cue: str | None = None,
    ):
        self.visual = visual or assessment()
        self.caption_text = caption
        self.is_safe = is_safe
        self.purpose_evidence = purpose_evidence
        self.person_presentation = person_presentation
        self.scene_context = scene_context
        self.solar_visual_cue = solar_visual_cue or (
            "panel" if purpose_evidence in {
                "photovoltaic_product", "solar_related_product"
            } else "none"
        )
        self.analysis_calls = 0
        self.fail_analysis = False

    async def analyze(self, image, *, purpose, title, max_alt_chars):
        self.analysis_calls += 1
        self.last_max_alt_chars = max_alt_chars
        if self.fail_analysis:
            raise ProviderError("Falha de teste")
        return ImageObservation(
            ImageAnalysisSuggestion(
                **self.visual.model_dump(),
                is_safe=self.is_safe,
                purpose_evidence=self.purpose_evidence,
                person_presentation=self.person_presentation,
                scene_context=self.scene_context,
                solar_visual_cue=self.solar_visual_cue,
                legenda_acessivel=self.caption_text,
            ),
            False,
        )


class FakeAuthVerifier:
    async def verify(self, token):
        if token == "auth-unavailable":
            raise AuthKeysUnavailable
        if token != "test-access-token":
            raise InvalidAccessToken


class PolicyTests(unittest.TestCase):
    def test_fallback_records_both_attempted_models(self) -> None:
        primary = FakeProvider()
        primary.fail_analysis = True
        fallback = FakeProvider()
        fallback.model_version = "test/fallback"
        result = asyncio.run(analyze_image(
            valid_image(),
            provider=primary,
            fallback_provider=fallback,
            request_id="fallback-test",
            purpose="product",
        ))
        self.assertEqual(result.decision, Decision.APPROVED)
        self.assertEqual(result.model_version, "test/fake+test/fallback")
        self.assertEqual(fallback.analysis_calls, 1)

        fallback.fail_analysis = True
        unavailable = asyncio.run(analyze_image(
            valid_image(),
            provider=primary,
            fallback_provider=fallback,
            request_id="fallback-failure",
            purpose="product",
        ))
        self.assertEqual(unavailable.reason_codes, ["PROVIDER_UNAVAILABLE"])
        self.assertEqual(unavailable.model_version, "test/fake+test/fallback")

    def test_purpose_evidence_must_match_scene_and_product_cue(self) -> None:
        cases = (
            ("company_profile", "company_facility", "domestic_or_leisure",
             "none", Decision.REJECTED, ["PURPOSE_MISMATCH"]),
            ("company_profile", "company_branding", "domestic_or_leisure",
             "none", Decision.REVIEW_REQUIRED, ["PURPOSE_UNCERTAIN"]),
            ("company_profile", "company_facility", "unclear",
             "none", Decision.REVIEW_REQUIRED, ["PURPOSE_UNCERTAIN"]),
            ("product", "photovoltaic_product", "neutral",
             "none", Decision.REVIEW_REQUIRED, ["ANALYSIS_INCONSISTENT"]),
            ("product", "solar_related_product", "neutral",
             "unclear", Decision.REVIEW_REQUIRED, ["PURPOSE_UNCERTAIN"]),
            ("product", "unrelated", "neutral",
             "panel", Decision.REVIEW_REQUIRED, ["ANALYSIS_INCONSISTENT"]),
        )
        for purpose, evidence, scene, cue, expected, reasons in cases:
            with self.subTest(purpose=purpose, evidence=evidence, scene=scene, cue=cue):
                result = asyncio.run(analyze_image(
                    valid_image(),
                    provider=FakeProvider(
                        purpose_evidence=evidence,
                        scene_context=scene,
                        solar_visual_cue=cue,
                    ),
                    request_id="evidence-test",
                    purpose=purpose,
                ))
                self.assertEqual(result.decision, expected)
                self.assertEqual(result.reason_codes, reasons)
                self.assertIsNone(result.alt_text)

    def test_purpose_rules_use_visible_evidence(self) -> None:
        cases = (
            ("company_profile", "person_portrait", "ordinary_clothing",
             Decision.REJECTED, ["PURPOSE_MISMATCH"]),
            ("company_profile", "company_branding", "not_applicable",
             Decision.APPROVED, []),
            ("company_profile", "company_facility", "not_applicable",
             Decision.APPROVED, []),
            ("professional_profile", "person_portrait", "ordinary_clothing",
             Decision.APPROVED, []),
            ("professional_profile", "person_portrait", "workwear",
             Decision.APPROVED, []),
            ("professional_profile", "person_portrait", "bare_torso_or_underwear",
             Decision.REJECTED, ["PURPOSE_MISMATCH"]),
            ("professional_profile", "person_portrait", "unclear",
             Decision.REVIEW_REQUIRED, ["PURPOSE_UNCERTAIN"]),
            ("product", "solar_related_product", "not_applicable",
             Decision.APPROVED, []),
            ("product", "unrelated", "not_applicable",
             Decision.REJECTED, ["PURPOSE_MISMATCH"]),
            ("product", "unclear", "not_applicable",
             Decision.REVIEW_REQUIRED, ["PURPOSE_UNCERTAIN"]),
            ("other", "photovoltaic_product", "not_applicable",
             Decision.REVIEW_REQUIRED, ["PURPOSE_UNSUPPORTED"]),
        )
        for purpose, evidence, presentation, expected, reasons in cases:
            with self.subTest(purpose=purpose, evidence=evidence, presentation=presentation):
                provider = FakeProvider(
                    purpose_evidence=evidence,
                    person_presentation=presentation,
                )
                result = asyncio.run(analyze_image(
                    valid_image(),
                    provider=provider,
                    request_id="purpose-test",
                    purpose=purpose,
                ))
                self.assertEqual(result.decision, expected)
                self.assertEqual(result.reason_codes, reasons)
                self.assertEqual(result.risk_categories, [])
                self.assertEqual(result.alt_text is not None, expected == Decision.APPROVED)

    def test_unsafe_label_without_risk_requires_review(self) -> None:
        provider = FakeProvider(is_safe=False)
        result = asyncio.run(
            analyze_image(
                valid_image(),
                provider=provider,
                request_id="inconsistent-risk",
                purpose="product",
            )
        )
        self.assertEqual(result.decision, Decision.REVIEW_REQUIRED)
        self.assertEqual(result.reason_codes, ["ANALYSIS_INCONSISTENT"])
        self.assertEqual(result.risk_categories, [])
        self.assertIsNone(result.alt_text)

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
        self.assertEqual(provider.analysis_calls, 1)

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
        provider.fail_analysis = True
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
                google_api_key="test-key"
            )
            provider = FakeProvider()
            app = create_app(settings, provider, auth_verifier=FakeAuthVerifier())
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

                old_token = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files=files,
                    headers={"Authorization": "Bearer test-secret"},
                )
                self.assertEqual(old_token.status_code, 401)

                auth_unavailable = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files=files,
                    headers={"Authorization": "Bearer auth-unavailable"},
                )
                self.assertEqual(auth_unavailable.status_code, 503)
                self.assertEqual(provider.analysis_calls, 0)

                authorized = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files=files,
                    headers={"Authorization": "Bearer test-access-token"},
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
                    headers={"Authorization": "Bearer test-access-token"},
                )
                self.assertEqual(larger_limit.status_code, 200)

                invalid_limit = await client.post(
                    "/v1/images/analyze",
                    data={**payload, "max_alt_chars": "0"},
                    files=files,
                    headers={"Authorization": "Bearer test-access-token"},
                )
                self.assertEqual(invalid_limit.status_code, 422)

                unsupported_purpose = await client.post(
                    "/v1/images/analyze",
                    data={**payload, "purpose": "other"},
                    files=files,
                    headers={"Authorization": "Bearer test-access-token"},
                )
                self.assertEqual(unsupported_purpose.status_code, 422)

                invalid = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files={"image": ("fake.jpg", b"not an image", "image/jpeg")},
                    headers={"Authorization": "Bearer test-access-token"},
                )
                self.assertEqual(invalid.json()["decision"], "rejected")
                self.assertEqual(invalid.json()["reason_codes"], ["INVALID_IMAGE"])

                too_large = await client.post(
                    "/v1/images/analyze",
                    data=payload,
                    files={
                        "image": ("huge.jpg", b"x" * (9 * 1024 * 1024), "image/jpeg")
                    },
                    headers={"Authorization": "Bearer test-access-token"},
                )
                self.assertEqual(too_large.status_code, 413)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
