import asyncio
import unittest
from io import BytesIO

import httpx
from ai_lib.registry import (
    KeyLease,
    RegistryClient,
    RegistryKeysUnavailable,
    RetryPolicy,
)
from PIL import Image

from src.agents.base.image_analysis_agent import (
    ImageObservation,
    ProviderBlocked,
    ProviderError,
)
from src.core.config import Settings
from src.image.validation import validate_image
from src.moderation.models import ImageAnalysisSuggestion
from src.providers.registry import RegistryImageAnalysisProvider


def sample_image():
    output = BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format="PNG")
    return validate_image(output.getvalue())


class FakeRegistry:
    def __init__(self, leases):
        self.leases = list(leases)
        self.lease_calls: list[tuple[str, ...]] = []
        self.reports: list[tuple[str, str]] = []

    async def alease(self, *, provider=None, purpose=None, exclude=()):
        self.lease_calls.append(tuple(exclude))
        item = self.leases.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def areport(self, key_id, outcome, retry_after_seconds=None):
        self.reports.append((key_id, outcome))


class FakeInnerProvider:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.model_version = "gemini/fake"

    async def analyze(self, image, *, purpose, title, max_alt_chars):
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class RegistryProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(registry_url="http://registry.test", registry_token="tok")
        self.observation = ImageObservation(
            ImageAnalysisSuggestion.model_construct(), safety_flagged=False
        )

    def provider(self, registry, inner_outcomes):
        provider = RegistryImageAnalysisProvider(self.settings, registry)
        outcomes = list(inner_outcomes)
        provider._provider_for = lambda lease: FakeInnerProvider(outcomes)
        return provider

    def run_analysis(self, provider):
        return asyncio.run(
            provider.analyze(sample_image(), purpose="product", title="", max_alt_chars=150)
        )

    def test_success_reports_ok_and_exposes_model_version(self) -> None:
        registry = FakeRegistry([_lease("k1")])
        result = self.run_analysis(self.provider(registry, [self.observation]))
        self.assertEqual(result.model_version, "gemini/fake")
        self.assertEqual(registry.reports, [("k1", "ok")])

    def test_rate_limited_key_is_reported_and_replaced(self) -> None:
        registry = FakeRegistry([_lease("k1"), _lease("k2")])
        result = self.run_analysis(
            self.provider(registry, [ProviderError("x", status_code=429), self.observation])
        )
        self.assertIsNotNone(result)
        self.assertEqual(registry.lease_calls, [(), ("k1",)])
        self.assertEqual(registry.reports, [("k1", "rate_limited"), ("k2", "ok")])

    def test_invalid_key_is_reported(self) -> None:
        registry = FakeRegistry([_lease("k1"), _lease("k2")])
        self.run_analysis(self.provider(registry, [ProviderError("x", status_code=401), self.observation]))
        self.assertEqual(registry.reports[0], ("k1", "invalid"))

    def test_gives_up_after_max_attempts(self) -> None:
        registry = FakeRegistry([_lease("k1"), _lease("k2"), _lease("k3")])
        failures = [ProviderError("x", status_code=500) for _ in range(3)]
        with self.assertRaises(ProviderError):
            self.run_analysis(self.provider(registry, failures))
        self.assertEqual(registry.lease_calls, [(), ("k1",), ("k1", "k2")])
        self.assertEqual(registry.reports, [])

    def test_safety_block_is_not_retried(self) -> None:
        registry = FakeRegistry([_lease("k1"), _lease("k2")])
        with self.assertRaises(ProviderBlocked):
            self.run_analysis(self.provider(registry, [ProviderBlocked("x")]))
        self.assertEqual(len(registry.lease_calls), 1)
        self.assertEqual(registry.reports, [("k1", "ok")])

    def test_registry_down_is_provider_error(self) -> None:
        registry = FakeRegistry([RegistryKeysUnavailable("down")])
        with self.assertRaises(ProviderError):
            self.run_analysis(self.provider(registry, []))


class RegistryWiringTests(unittest.TestCase):
    """O provider conversa com o corretor pelo cliente real da solaria-lib."""

    def lease_json(self, key_id: str) -> dict[str, object]:
        return {
            "provider": "gemini",
            "key_id": key_id,
            "api_key": f"secret-{key_id}",
            "base_url": "https://example.test",
            "auth_header": {"name": "x", "value": "y"},
        }

    def test_leases_with_exclusion_and_reports_through_the_lib_client(self) -> None:
        seen: list[httpx.Request] = []
        leases = [self.lease_json("k1"), self.lease_json("k2")]

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/report"):
                return httpx.Response(204)
            return httpx.Response(200, json=leases.pop(0))

        registry = RegistryClient(
            "http://registry.test",
            "tok",
            policy=RetryPolicy(timeout=5),
            async_transport=httpx.MockTransport(handler),
        )
        settings = Settings(registry_url="http://registry.test", registry_token="tok")
        provider = RegistryImageAnalysisProvider(settings, registry)
        outcomes = [
            ProviderError("x", status_code=429),
            ImageObservation(ImageAnalysisSuggestion.model_construct(), safety_flagged=False),
        ]
        provider._provider_for = lambda lease: FakeInnerProvider(outcomes)

        asyncio.run(provider.analyze(sample_image(), purpose="product", title="", max_alt_chars=150))

        lease_calls = [r for r in seen if r.url.path == "/v1/llm/keys"]
        self.assertEqual(lease_calls[0].headers["authorization"], "Bearer tok")
        self.assertEqual(lease_calls[1].url.params.get_list("exclude"), ["k1"])
        reports = [(r.url.path, r.content) for r in seen if r.url.path.endswith("/report")]
        self.assertEqual(
            reports,
            [
                ("/v1/llm/keys/k1/report", b'{"outcome":"rate_limited"}'),
                ("/v1/llm/keys/k2/report", b'{"outcome":"ok"}'),
            ],
        )


def _lease(key_id: str):
    return KeyLease(
        provider="gemini",
        key_id=key_id,
        api_key=f"secret-{key_id}",
        base_url="https://example.test",
        auth_header={"name": "x", "value": "y"},
    )


if __name__ == "__main__":
    unittest.main()
