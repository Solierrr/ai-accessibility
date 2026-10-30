"""Rota de debug: acesso de admin e etapas registradas, sem chamadas externas."""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from src.api.app import create_app
from src.core.config import Settings
from src.providers.gemini import ProviderBlocked, ProviderError
from tests.test_analysis import FakeProvider as BaseProvider
from tests.test_analysis import assessment, valid_image

ADMIN = "11111111-1111-4111-8111-111111111111"
OUTRO = "22222222-2222-4222-8222-222222222222"
SERVICE_KEY = "s" * 32
ADMIN_TOKEN = "jwt-admin"
CAPTION = "Painel solar azul sobre uma mesa."
DEBUG = "/debug/images/analyze"


class FakeProvider(BaseProvider):
    """Provedor falso com versão própria e falha de moderação configurável."""

    def __init__(
        self,
        model_version: str = "test/fake",
        *,
        visual=None,
        moderation_error: ProviderError | None = None,
    ) -> None:
        super().__init__(visual=visual)
        self.model_version = model_version
        self.moderation_error = moderation_error

    async def moderate(self, image, *, prompt=None, capture=None):
        if self.moderation_error is not None:
            if capture is not None:
                record = capture.begin(
                    prompt or "MODERATION-PROMPT", self.model_version
                )
                record.fail(self.moderation_error)
            raise self.moderation_error
        return await super().moderate(image, prompt=prompt, capture=capture)


def fake_decode(token: str, *, jwks_url: str, issuer: str) -> str | None:
    return ADMIN if token == ADMIN_TOKEN else None


def make_settings(**overrides: object) -> Settings:
    data = {
        "google_api_key": "test-key",
        "service_keys": {"api-core": SERVICE_KEY},
        "environment": "QA",
        "debug_admin_subs": ADMIN,
        "jwt_jwks_url": "http://jwks.test/.well-known/jwks.json",
        "jwt_issuer": "solaria-auth",
    }
    data.update(overrides)
    return Settings(**data)


def post_debug(
    settings: Settings,
    provider: FakeProvider | None = None,
    *,
    fallback: FakeProvider | None = None,
    token: str | None = ADMIN_TOKEN,
    content: bytes | None = None,
):
    async def run() -> httpx.Response:
        app = create_app(settings, provider or FakeProvider(), fallback)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                DEBUG,
                data={"purpose": "product"},
                files={"image": ("panel.png", content or valid_image(), "image/png")},
                headers=headers,
            )

    with patch("src.api.debug_access.decode_user_id", fake_decode):
        return asyncio.run(run())


class AccessTests(unittest.TestCase):
    def test_without_admins_the_route_does_not_exist(self) -> None:
        self.assertEqual(
            post_debug(make_settings(debug_admin_subs="")).status_code, 404
        )

    def test_environment_must_be_allowed(self) -> None:
        for environment in ("PRD", "staging", ""):
            with self.subTest(environment=environment):
                response = post_debug(make_settings(environment=environment))
                self.assertEqual(response.status_code, 404)

    def test_allowed_environments_ignore_case_and_spaces(self) -> None:
        for environment in ("dev", " qa ", "prod"):
            with self.subTest(environment=environment):
                response = post_debug(make_settings(environment=environment))
                self.assertEqual(response.status_code, 200)

    def test_missing_or_invalid_token_is_401(self) -> None:
        self.assertEqual(post_debug(make_settings(), token=None).status_code, 401)
        self.assertEqual(post_debug(make_settings(), token="outro").status_code, 401)

    def test_service_key_does_not_open_the_route(self) -> None:
        response = post_debug(make_settings(), token=SERVICE_KEY)
        self.assertEqual(response.status_code, 401)

    def test_without_jwt_configuration_is_401(self) -> None:
        settings = make_settings(jwt_jwks_url=None, jwt_issuer=None)
        self.assertEqual(post_debug(settings).status_code, 401)

    def test_user_outside_the_list_is_403(self) -> None:
        self.assertEqual(
            post_debug(make_settings(debug_admin_subs=OUTRO)).status_code, 403
        )

    def test_list_with_garbage_and_uppercase_still_works(self) -> None:
        settings = make_settings(debug_admin_subs=f" lixo ,{OUTRO},, {ADMIN.upper()} ")
        self.assertEqual(post_debug(settings).status_code, 200)

    def test_route_is_not_in_the_openapi(self) -> None:
        app = create_app(make_settings(), FakeProvider())
        self.assertFalse(
            any(path.startswith("/debug") for path in app.openapi()["paths"])
        )

    def test_oversized_upload_is_rejected_with_413(self) -> None:
        response = post_debug(make_settings(), content=b"0" * (9 * 1024 * 1024))
        self.assertEqual(response.status_code, 413)


class StepTests(unittest.TestCase):
    @staticmethod
    def steps(response: httpx.Response) -> list[dict]:
        return response.json()["debug"]["nodes"]

    def test_approved_flow_lists_every_stage_without_media_or_text(self) -> None:
        response = post_debug(make_settings())

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["decision"], "approved")
        self.assertEqual(
            [node["node"] for node in body["debug"]["nodes"]],
            [
                "image_validation",
                "moderation",
                "policy",
                "caption",
                "caption_validation",
            ],
        )
        self.assertFalse(body["debug"]["persisted"])
        self.assertEqual(body["debug"]["environment"], "QA")
        dumped = json.dumps(body["debug"], ensure_ascii=False)
        self.assertNotIn(CAPTION, dumped)
        self.assertNotIn("base64", dumped)

    def test_invalid_image_stops_at_image_validation(self) -> None:
        response = post_debug(make_settings(), content=b"nao e imagem")

        self.assertEqual(response.status_code, 200)
        steps = self.steps(response)
        self.assertEqual([step["node"] for step in steps], ["image_validation"])
        self.assertEqual(steps[0]["output"]["outcome"], "rejected")
        self.assertNotIn("run_id", response.json()["debug"])

    def test_policy_rejection_never_calls_the_caption(self) -> None:
        provider = FakeProvider(visual=assessment(graphic_violence="explicit"))

        response = post_debug(make_settings(), provider)

        stages = [step["node"] for step in self.steps(response)]
        self.assertEqual(stages, ["image_validation", "moderation", "policy"])
        self.assertEqual(self.steps(response)[-1]["output"]["outcome"], "rejected")
        self.assertEqual(provider.caption_calls, 0)

    def test_moderation_failure_with_fallback_shows_error_then_ok(self) -> None:
        primary = FakeProvider(
            "test/primary", moderation_error=ProviderError("falha", status_code=503)
        )

        response = post_debug(
            make_settings(), primary, fallback=FakeProvider("test/fallback")
        )

        moderation = [s for s in self.steps(response) if s["node"] == "moderation"]
        self.assertEqual([s["output"]["outcome"] for s in moderation], ["error", "ok"])
        self.assertEqual(moderation[0]["output"]["provider"], "test/primary")
        self.assertEqual(moderation[1]["output"]["provider"], "test/fallback")
        self.assertEqual([s["status"] for s in moderation], ["error", "ok"])

    def test_provider_safety_block_shows_as_blocked(self) -> None:
        provider = FakeProvider(moderation_error=ProviderBlocked("bloqueado"))

        response = post_debug(make_settings(), provider)

        self.assertEqual(response.json()["decision"], "review_required")
        self.assertEqual(self.steps(response)[-1]["output"]["outcome"], "blocked")

    def test_provider_failure_keeps_only_the_status_code(self) -> None:
        provider = FakeProvider(
            moderation_error=ProviderError("segredo-do-provedor", status_code=429)
        )

        response = post_debug(make_settings(), provider)

        failure = self.steps(response)[-1]
        self.assertEqual(failure["output"]["status_code"], 429)
        self.assertEqual(failure["error"], "ProviderError (HTTP 429)")
        self.assertNotIn("segredo-do-provedor", json.dumps(response.json()["debug"]))


if __name__ == "__main__":
    unittest.main()
