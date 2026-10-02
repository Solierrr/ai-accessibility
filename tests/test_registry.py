import asyncio
import unittest
from io import BytesIO

import httpx
from PIL import Image

from src.agents.base.image_analysis_agent import (
    ImageObservation,
    ProviderBlocked,
    ProviderError,
)
from src.clients.registry import (
    NoKeyAvailable,
    RegistryClient,
    RegistryConfig,
    RegistryUnavailable,
)
from src.core.config import Settings
from src.image.validation import validate_image
from src.moderation.models import ImageAnalysisSuggestion
from src.providers.registry import RegistryImageAnalysisProvider


def sample_image():
    output = BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format="PNG")
    return validate_image(output.getvalue())


def lease_body(key_id: str, provider: str = "gemini") -> dict[str, object]:
    return {
        "provider": provider,
        "key_id": key_id,
        "api_key": f"secret-{key_id}",
        "base_url": "https://example.test",
        "auth_header": {"name": "x", "value": "y"},
    }


class FakeRegistry:
    def __init__(self, leases):
        self.leases = list(leases)
        self.lease_calls: list[tuple[str, ...]] = []
        self.reports: list[tuple[str, str]] = []

    async def lease(self, *, provider=None, purpose=None, exclude=()):
        self.lease_calls.append(tuple(exclude))
        item = self.leases.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def report(self, key_id, outcome, retry_after_seconds=None):
        self.reports.append((key_id, outcome))


def make_client(handler, *, retries=2):
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = RegistryClient(
        RegistryConfig(base_url="http://registry.test", token="tok", retries=retries),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )
    return client, sleeps


class RegistryClientTests(unittest.TestCase):
    def test_lease_sends_token_and_exclusions(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=lease_body("k1"))

        client, _ = make_client(handler)
        lease = asyncio.run(client.lease(exclude=("a", "b")))
        self.assertEqual((lease.provider, lease.key_id, lease.api_key), ("gemini", "k1", "secret-k1"))
        self.assertEqual(seen[0].headers["authorization"], "Bearer tok")
        self.assertEqual(seen[0].url.params.get_list("exclude"), ["a", "b"])
        self.assertNotIn("secret-k1", repr(lease))

    def test_retries_503_with_backoff_then_succeeds(self) -> None:
        responses = [httpx.Response(503), httpx.Response(503), httpx.Response(200, json=lease_body("k1"))]
        client, sleeps = make_client(lambda request: responses.pop(0))
        lease = asyncio.run(client.lease())
        self.assertEqual(lease.key_id, "k1")
        self.assertEqual(sleeps, [1.0, 3.0])

    def test_respects_retry_after_from_503(self) -> None:
        responses = [
            httpx.Response(503, headers={"Retry-After": "5"}),
            httpx.Response(200, json=lease_body("k1")),
        ]
        client, sleeps = make_client(lambda request: responses.pop(0))
        asyncio.run(client.lease())
        self.assertEqual(sleeps, [5.0])

    def test_long_retry_after_fails_without_waiting(self) -> None:
        client, sleeps = make_client(lambda request: httpx.Response(503, headers={"Retry-After": "60"}))
        with self.assertRaises(NoKeyAvailable):
            asyncio.run(client.lease())
        self.assertEqual(sleeps, [])

    def test_exhausted_retries_with_retry_after_means_no_key(self) -> None:
        client, _ = make_client(lambda request: httpx.Response(503, headers={"Retry-After": "2"}))
        with self.assertRaises(NoKeyAvailable):
            asyncio.run(client.lease())

    def test_unauthorized_and_unreachable_registry_fail_closed(self) -> None:
        client, sleeps = make_client(lambda request: httpx.Response(401))
        with self.assertRaises(RegistryUnavailable):
            asyncio.run(client.lease())
        self.assertEqual(sleeps, [])

        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        client, sleeps = make_client(unreachable)
        with self.assertRaises(RegistryUnavailable):
            asyncio.run(client.lease())
        self.assertEqual(sleeps, [1.0, 3.0])

    def test_not_configured_registry_is_no_key(self) -> None:
        client, _ = make_client(lambda request: httpx.Response(404))
        with self.assertRaises(NoKeyAvailable):
            asyncio.run(client.lease())

    def test_report_posts_outcome_and_never_raises(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(500)

        client, sleeps = make_client(handler)
        asyncio.run(client.report("k1", "rate_limited"))
        self.assertEqual(seen[0].url.path, "/v1/llm/keys/k1/report")
        self.assertEqual(seen[0].content, b'{"outcome":"rate_limited"}')
        self.assertEqual(len(seen), 1)
        self.assertEqual(sleeps, [])


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
        registry = FakeRegistry([RegistryUnavailable("down")])
        with self.assertRaises(ProviderError):
            self.run_analysis(self.provider(registry, []))


def _lease(key_id: str):
    from src.clients.registry import KeyLease

    return KeyLease(provider="gemini", key_id=key_id, api_key=f"secret-{key_id}")


if __name__ == "__main__":
    unittest.main()
