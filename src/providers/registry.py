import logging

from src.agents.base.image_analysis_agent import (
    ImageAnalysisProvider,
    ImageObservation,
    ProviderBlocked,
    ProviderError,
    ProviderInvalidOutput,
)
from src.clients.registry import KeyLease, Outcome, RegistryClient, RegistryError
from src.core.config import Settings
from src.image.validation import ValidatedImage
from src.providers.gemini import GeminiImageAnalysisProvider
from src.providers.groq import GroqImageAnalysisProvider

logger = logging.getLogger(__name__)

_RATE_LIMITED_STATUS = {429}
_INVALID_KEY_STATUS = {401, 403}


class RegistryImageAnalysisProvider:
    """Pega uma chave do google-registry a cada análise e troca de chave em falha técnica."""

    model_version = "registry"

    def __init__(self, settings: Settings, registry: RegistryClient) -> None:
        self.settings = settings
        self.registry = registry
        self.max_attempts = settings.registry_max_attempts

    def _provider_for(self, lease: KeyLease) -> ImageAnalysisProvider:
        if lease.provider == "gemini":
            return GeminiImageAnalysisProvider(self.settings, api_key=lease.api_key)
        if lease.provider == "groq":
            return GroqImageAnalysisProvider(self.settings, api_key=lease.api_key)
        raise ProviderError("Provedor desconhecido no registry")

    async def analyze(
        self, image: ValidatedImage, *, purpose: str, title: str, max_alt_chars: int
    ) -> ImageObservation:
        excluded: list[str] = []
        failure: ProviderError | None = None
        for _ in range(self.max_attempts):
            try:
                lease = await self.registry.lease(exclude=tuple(excluded))
            except RegistryError as exc:
                raise failure or ProviderError("Chave de IA indisponível no registry") from exc

            try:
                provider = self._provider_for(lease)
                observation = await provider.analyze(
                    image, purpose=purpose, title=title, max_alt_chars=max_alt_chars
                )
            except (ProviderBlocked, ProviderInvalidOutput):
                await self.registry.report(lease.key_id, "ok")
                raise
            except ProviderError as exc:
                failure = exc
                excluded.append(lease.key_id)
                logger.warning(
                    "registry_key_failure provider=%s status=%s", lease.provider, exc.status_code
                )
                outcome = _outcome_for(exc.status_code)
                if outcome:
                    await self.registry.report(lease.key_id, outcome)
                continue

            await self.registry.report(lease.key_id, "ok")
            return ImageObservation(
                observation.suggestion,
                observation.safety_flagged,
                model_version=provider.model_version,
            )
        raise failure or ProviderError("Falha na chamada ao provedor")


def _outcome_for(status_code: int | None) -> Outcome | None:
    if status_code in _RATE_LIMITED_STATUS:
        return "rate_limited"
    if status_code in _INVALID_KEY_STATUS:
        return "invalid"
    return None
