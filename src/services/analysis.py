"""Orquestra etapas, sempre falhando para revisão ou rejeição."""

import logging

from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from src.caption.validation import DEFAULT_ALT_TEXT_CHARS, validated_alt_text
from src.image.validation import ImageValidationError, validate_image
from src.moderation.policy import POLICY_VERSION, Decision, decide
from src.providers.gemini import ProviderBlocked, ProviderError, VisionProvider

logger = logging.getLogger(__name__)


def log_provider_failure(
    provider_name: str, stage: str, exc: ProviderError
) -> None:
    """Registra somente metadados seguros; nunca imagem, prompt ou resposta bruta."""
    cause_type = type(exc.__cause__).__name__ if exc.__cause__ else "none"
    logger.warning(
        "provider_failure provider=%s stage=%s status=%s cause_type=%s",
        provider_name,
        stage,
        exc.status_code,
        cause_type,
    )


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    analysis_id: str
    request_id: str
    decision: Decision
    reason_codes: list[str]
    risk_categories: list[str]
    alt_text: str | None
    policy_version: str
    model_version: str


async def analyze_image(
    content: bytes,
    *,
    provider: VisionProvider,
    fallback_provider: VisionProvider | None = None,
    request_id: str,
    purpose: str,
    max_alt_chars: int = DEFAULT_ALT_TEXT_CHARS,
    context_title: str = "",
) -> AnalysisResponse:
    analysis_id = str(uuid4())
    used_models: list[str] = []

    def result(
        decision: Decision,
        reasons: tuple[str, ...] = (),
        categories: tuple[str, ...] = (),
        alt_text: str | None = None,
    ) -> AnalysisResponse:
        return AnalysisResponse(
            analysis_id=analysis_id,
            request_id=request_id,
            decision=decision,
            reason_codes=list(reasons),
            risk_categories=list(categories),
            alt_text=alt_text,
            policy_version=POLICY_VERSION,
            model_version="+".join(used_models) or provider.model_version,
        )

    try:
        image = validate_image(content)
    except ImageValidationError as exc:
        return result(Decision.REJECTED, (exc.code,))

    moderation_provider = provider
    try:
        observation = await moderation_provider.moderate(image)
    except ProviderBlocked:
        return result(Decision.REVIEW_REQUIRED, ("PROVIDER_SAFETY_FLAG",))
    except ProviderError as exc:
        log_provider_failure(moderation_provider.model_version, "moderation", exc)
        if fallback_provider is None:
            return result(Decision.REVIEW_REQUIRED, ("PROVIDER_UNAVAILABLE",))
        moderation_provider = fallback_provider
        try:
            observation = await moderation_provider.moderate(image)
        except ProviderBlocked:
            return result(Decision.REVIEW_REQUIRED, ("PROVIDER_SAFETY_FLAG",))
        except ProviderError as fallback_exc:
            log_provider_failure(
                moderation_provider.model_version, "moderation", fallback_exc
            )
            return result(Decision.REVIEW_REQUIRED, ("PROVIDER_UNAVAILABLE",))

    used_models.append(moderation_provider.model_version)

    policy = decide(observation.assessment, safety_flagged=observation.safety_flagged)
    if policy.decision != Decision.APPROVED:
        return result(policy.decision, policy.reason_codes, policy.categories)

    caption_provider = moderation_provider
    try:
        suggestion = await caption_provider.caption(
            image,
            purpose=purpose,
            title=context_title,
            max_alt_chars=max_alt_chars,
        )
    except ProviderBlocked:
        return result(Decision.REVIEW_REQUIRED, ("PROVIDER_SAFETY_FLAG",))
    except ProviderError as exc:
        log_provider_failure(caption_provider.model_version, "caption", exc)
        if fallback_provider is None or caption_provider is fallback_provider:
            return result(Decision.REVIEW_REQUIRED, ("PROVIDER_UNAVAILABLE",))
        caption_provider = fallback_provider
        try:
            suggestion = await caption_provider.caption(
                image,
                purpose=purpose,
                title=context_title,
                max_alt_chars=max_alt_chars,
            )
        except ProviderBlocked:
            return result(Decision.REVIEW_REQUIRED, ("PROVIDER_SAFETY_FLAG",))
        except ProviderError as fallback_exc:
            log_provider_failure(
                caption_provider.model_version, "caption", fallback_exc
            )
            return result(Decision.REVIEW_REQUIRED, ("PROVIDER_UNAVAILABLE",))

    if caption_provider.model_version != moderation_provider.model_version:
        used_models.append(caption_provider.model_version)
    alt_text = validated_alt_text(suggestion, max_alt_chars=max_alt_chars)

    if alt_text is None:
        return result(Decision.REVIEW_REQUIRED, ("CAPTION_INVALID",))
    return result(Decision.APPROVED, alt_text=alt_text)
