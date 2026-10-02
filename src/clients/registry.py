import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

Outcome = Literal["ok", "rate_limited", "invalid"]
_RETRYABLE_STATUS = {502, 503, 504}
_MAX_RETRY_AFTER_SECONDS = 10.0


class RegistryError(Exception):
    """Falha ao obter chave do google-registry, sem segredos na mensagem."""


class RegistryUnavailable(RegistryError):
    """Registry inacessível ou com resposta inesperada."""


class NoKeyAvailable(RegistryError):
    """Registry sem chave configurada ou disponível para o pedido."""


@dataclass(frozen=True, slots=True)
class KeyLease:
    provider: str
    key_id: str
    api_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RegistryConfig:
    base_url: str
    token: str = field(repr=False)
    timeout_seconds: float = 10.0
    retries: int = 2
    backoff_seconds: tuple[float, ...] = (1.0, 3.0)


class RegistryClient:
    """Cliente do corretor de chaves de LLM do google-registry (lease por chamada + report)."""

    def __init__(
        self,
        config: RegistryConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self._transport = transport
        self._sleep = sleep

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.config.token}"},
            timeout=self.config.timeout_seconds,
            transport=self._transport,
        )

    async def lease(
        self,
        *,
        provider: str | None = None,
        purpose: str | None = None,
        exclude: tuple[str, ...] = (),
    ) -> KeyLease:
        params: list[tuple[str, str]] = [("exclude", key_id) for key_id in exclude]
        if provider:
            params.append(("provider", provider))
        if purpose:
            params.append(("purpose", purpose))
        response = await self._request("GET", "/v1/llm/keys", params=params)
        try:
            data = response.json()
            return KeyLease(
                provider=str(data["provider"]),
                key_id=str(data["key_id"]),
                api_key=str(data["api_key"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise RegistryUnavailable("Resposta inválida do registry") from exc

    async def report(
        self, key_id: str, outcome: Outcome, retry_after_seconds: int | None = None
    ) -> None:
        body: dict[str, object] = {"outcome": outcome}
        if retry_after_seconds:
            body["retry_after_seconds"] = retry_after_seconds
        try:
            await self._request(
                "POST", f"/v1/llm/keys/{key_id}/report", json=body, retries=0
            )
        except RegistryError:
            logger.warning("registry_report_failed outcome=%s", outcome)

    async def _request(
        self, method: str, path: str, *, retries: int | None = None, **kwargs: object
    ) -> httpx.Response:
        attempts = (self.config.retries if retries is None else retries) + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            wait = self._backoff(attempt)
            try:
                async with self._client() as client:
                    response = await client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                if last:
                    raise RegistryUnavailable("Registry inacessível") from exc
                await self._sleep(wait)
                continue

            if response.status_code < 400:
                return response
            if response.status_code == 404:
                raise NoKeyAvailable("Nenhuma chave configurada no registry")
            if response.status_code not in _RETRYABLE_STATUS:
                raise RegistryUnavailable(f"Registry respondeu {response.status_code}")

            retry_after = _retry_after(response)
            if retry_after is not None and retry_after > _MAX_RETRY_AFTER_SECONDS:
                raise NoKeyAvailable("Nenhuma chave disponível no momento")
            if last:
                if response.status_code == 503 and retry_after is not None:
                    raise NoKeyAvailable("Nenhuma chave disponível no momento")
                raise RegistryUnavailable(f"Registry respondeu {response.status_code}")
            await self._sleep(max(wait, retry_after or 0.0))
        raise RegistryUnavailable("Registry inacessível")

    def _backoff(self, attempt: int) -> float:
        steps = self.config.backoff_seconds
        if not steps:
            return 0.0
        return steps[min(attempt, len(steps) - 1)]


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None
