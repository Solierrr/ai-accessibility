"""Decisão determinística sobre sinais do provedor."""

from dataclasses import dataclass
from enum import StrEnum

from src.moderation.models import Confidence, Context, Severity, VisionAssessment

POLICY_VERSION = "v1"


class Decision(StrEnum):
    APPROVED = "approved"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: Decision
    reason_codes: tuple[str, ...]
    categories: tuple[str, ...]


_RISKS = (
    ("sexual_content", "SEXUAL_CONTENT"),
    ("graphic_violence", "GRAPHIC_VIOLENCE"),
    ("hateful_content", "HATE_CONTENT"),
    ("offensive_text", "OFFENSIVE_TEXT"),
)


def decide(
    assessment: VisionAssessment, *, safety_flagged: bool = False
) -> PolicyResult:
    """Nunca aprova incerteza, sinais de risco ou bloqueio de segurança do provedor."""
    categories = tuple(
        code for field, code in _RISKS if getattr(assessment, field) != Severity.NONE
    )
    severe = tuple(
        code
        for field, code in _RISKS[:3]
        if getattr(assessment, field) == Severity.EXPLICIT
    )
    if (
        severe
        and assessment.context == Context.ORDINARY
        and assessment.confidence == Confidence.HIGH
    ):
        return PolicyResult(Decision.REJECTED, severe, categories)

    if (
        categories
        or safety_flagged
        or assessment.uncertain
        or assessment.prompt_injection
        or assessment.confidence == Confidence.LOW
        or assessment.context == Context.UNCERTAIN
    ):
        reasons = categories or ("UNCERTAIN_CLASSIFICATION",)
        if safety_flagged and "PROVIDER_SAFETY_FLAG" not in reasons:
            reasons += ("PROVIDER_SAFETY_FLAG",)
        if assessment.prompt_injection:
            reasons += ("PROMPT_INJECTION",)
        return PolicyResult(
            Decision.REVIEW_REQUIRED, tuple(dict.fromkeys(reasons)), categories
        )

    return PolicyResult(Decision.APPROVED, (), ())
