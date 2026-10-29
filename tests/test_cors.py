import asyncio
import unittest
from io import BytesIO

import httpx
from PIL import Image

from src.api.app import create_app
from src.core.config import Settings, cors_origins

ORIGIN = "https://app.example.com"


def build_app(**overrides):
    settings = Settings(
        google_api_key=None,
        internal_api_token="test-secret",
        cors_allowed_origins=(ORIGIN,),
        **overrides,
    )
    return create_app(settings)


def png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(output, format="PNG")
    return output.getvalue()


class CorsTests(unittest.TestCase):
    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        async def run() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=build_app()), base_url="http://test"
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(run())

    def test_preflight_with_authorization_is_answered_without_token(self) -> None:
        response = self.request(
            "OPTIONS",
            "/v1/images/analyze",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        self.assertIn(
            "authorization", response.headers["access-control-allow-headers"].lower()
        )
        self.assertNotIn("access-control-allow-credentials", response.headers)

    def test_preflight_from_unknown_origin_is_refused(self) -> None:
        response = self.request(
            "OPTIONS",
            "/v1/images/analyze",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_unauthorized_response_carries_cors_headers(self) -> None:
        response = self.request(
            "POST",
            "/v1/images/analyze",
            data={"purpose": "product"},
            files={"image": ("panel.png", png(), "image/png")},
            headers={"Origin": ORIGIN, "Authorization": "Bearer invalid"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)

    def test_health_stays_public_with_cors(self) -> None:
        response = self.request("GET", "/health", headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)

    def test_origins_come_from_a_comma_separated_list(self) -> None:
        self.assertEqual(
            cors_origins(" https://a.com ,, https://b.com,"),
            ("https://a.com", "https://b.com"),
        )
        self.assertEqual(cors_origins(""), ())

    def test_default_origins_are_local_only(self) -> None:
        self.assertEqual(
            Settings(google_api_key=None, internal_api_token=None).cors_allowed_origins,
            ("http://localhost:5173", "http://localhost:3000"),
        )

    def test_settings_read_the_origins_from_the_environment(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": ORIGIN}):
            self.assertEqual(Settings.from_env().cors_allowed_origins, (ORIGIN,))


if __name__ == "__main__":
    unittest.main()
