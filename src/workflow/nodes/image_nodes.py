import logging

from src.agents.base.image_analysis_agent import (
    ImageAnalysisProvider,
    ProviderBlocked,
    ProviderError,
    ProviderInvalidOutput,
)
from src.caption.validation import validated_alt_text
from src.image.validation import ImageValidationError, validate_image
from src.moderation.models import CaptionSuggestion
from src.moderation.policy import Decision, decide
from src.purpose.policy import decide_purpose
from src.workflow.state.state import ImageAnalysisState

logger = logging.getLogger(__name__)


def _log_failure(provider: ImageAnalysisProvider, exc: ProviderError) -> None:
    logger.warning(
        "provider_failure provider=%s stage=analysis status=%s cause_type=%s",
        provider.model_version,
        exc.status_code,
        type(exc.__cause__).__name__ if exc.__cause__ else "none",
    )


def validate_node(state: ImageAnalysisState) -> ImageAnalysisState:
    try:
        return {"image": validate_image(state["content"]), "content": b""}
    except ImageValidationError as exc:
        return {"decision": Decision.REJECTED, "reason_codes": (exc.code,)}


def analysis_node(
    primary: ImageAnalysisProvider, fallback: ImageAnalysisProvider | None
):
    async def run(state: ImageAnalysisState) -> ImageAnalysisState:
        active = primary

        async def invoke(provider: ImageAnalysisProvider):
            return await provider.analyze(
                state["image"],
                purpose=state["purpose"],
                title=state["context_title"],
                max_alt_chars=state["max_alt_chars"],
            )

        try:
            observation = await invoke(active)
        except ProviderBlocked:
            return {
                "decision": Decision.REVIEW_REQUIRED,
                "reason_codes": ("PROVIDER_SAFETY_FLAG",),
                "used_models": [active.model_version],
            }
        except ProviderInvalidOutput:
            return {
                "decision": Decision.REVIEW_REQUIRED,
                "reason_codes": ("ANALYSIS_INVALID",),
                "used_models": [active.model_version],
            }
        except ProviderError as exc:
            _log_failure(active, exc)
            if fallback is None:
                return {
                    "decision": Decision.REVIEW_REQUIRED,
                    "reason_codes": ("PROVIDER_UNAVAILABLE",),
                    "used_models": [active.model_version],
                }
            attempted_models = [active.model_version, fallback.model_version]
            active = fallback
            try:
                observation = await invoke(active)
            except ProviderBlocked:
                return {
                    "decision": Decision.REVIEW_REQUIRED,
                    "reason_codes": ("PROVIDER_SAFETY_FLAG",),
                    "used_models": attempted_models,
                }
            except ProviderInvalidOutput:
                return {
                    "decision": Decision.REVIEW_REQUIRED,
                    "reason_codes": ("ANALYSIS_INVALID",),
                    "used_models": attempted_models,
                }
            except ProviderError as fallback_exc:
                _log_failure(active, fallback_exc)
                return {
                    "decision": Decision.REVIEW_REQUIRED,
                    "reason_codes": ("PROVIDER_UNAVAILABLE",),
                    "used_models": attempted_models,
                }
            return {"observation": observation, "used_models": attempted_models}
        return {
            "observation": observation,
            "used_models": [observation.model_version or active.model_version],
        }

    return run


def policy_node(state: ImageAnalysisState) -> ImageAnalysisState:
    if "decision" in state:
        return {"decision": state["decision"]}

    observation = state["observation"]
    suggestion = observation.suggestion
    policy = decide(suggestion.assessment(), safety_flagged=observation.safety_flagged)
    if policy.decision != Decision.APPROVED:
        return {
            "decision": policy.decision,
            "reason_codes": policy.reason_codes,
            "risk_categories": policy.categories,
        }
    purpose_policy = decide_purpose(state["purpose"], suggestion)
    if purpose_policy.decision != Decision.APPROVED:
        return {
            "decision": purpose_policy.decision,
            "reason_codes": purpose_policy.reason_codes,
        }

    if not suggestion.is_safe or suggestion.motivo_bloqueio:
        return {
            "decision": Decision.REVIEW_REQUIRED,
            "reason_codes": ("ANALYSIS_INCONSISTENT",),
        }

    caption = CaptionSuggestion(
        alt_text=suggestion.legenda_acessivel or "",
        uncertain=suggestion.uncertain,
    )
    alt_text = validated_alt_text(caption, max_alt_chars=state["max_alt_chars"])
    if alt_text is None:
        return {
            "decision": Decision.REVIEW_REQUIRED,
            "reason_codes": ("CAPTION_INVALID",),
        }
    return {"decision": Decision.APPROVED, "reason_codes": (), "alt_text": alt_text}
