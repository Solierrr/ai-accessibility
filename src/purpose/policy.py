from src.moderation.models import (
    ImageAnalysisSuggestion,
    PersonPresentation,
    PurposeEvidence,
    SceneContext,
    SolarVisualCue,
)
from src.moderation.policy import Decision, PolicyResult


_EXPECTED_EVIDENCE = {
    "company_profile": frozenset({
        PurposeEvidence.COMPANY_BRANDING,
        PurposeEvidence.COMPANY_FACILITY,
    }),
    "professional_profile": frozenset({PurposeEvidence.PERSON_PORTRAIT}),
    "product": frozenset({
        PurposeEvidence.PHOTOVOLTAIC_PRODUCT,
        PurposeEvidence.SOLAR_RELATED_PRODUCT,
    }),
}


def decide_purpose(purpose: str, suggestion: ImageAnalysisSuggestion) -> PolicyResult:
    expected = _EXPECTED_EVIDENCE.get(purpose)
    if expected is None:
        return PolicyResult(Decision.REVIEW_REQUIRED, ("PURPOSE_UNSUPPORTED",), ())

    evidence = suggestion.purpose_evidence
    if evidence == PurposeEvidence.UNCLEAR:
        return PolicyResult(Decision.REVIEW_REQUIRED, ("PURPOSE_UNCERTAIN",), ())
    if purpose == "product" and suggestion.solar_visual_cue not in {
        SolarVisualCue.NONE, SolarVisualCue.UNCLEAR
    } and evidence not in expected:
        return PolicyResult(Decision.REVIEW_REQUIRED, ("ANALYSIS_INCONSISTENT",), ())
    if evidence not in expected:
        return PolicyResult(Decision.REJECTED, ("PURPOSE_MISMATCH",), ())

    if purpose == "company_profile":
        if suggestion.scene_context == SceneContext.DOMESTIC_OR_LEISURE:
            decision = (Decision.REJECTED if evidence == PurposeEvidence.COMPANY_FACILITY
                        else Decision.REVIEW_REQUIRED)
            reason = ("PURPOSE_MISMATCH" if decision == Decision.REJECTED
                      else "PURPOSE_UNCERTAIN")
            return PolicyResult(decision, (reason,), ())
        if suggestion.scene_context == SceneContext.UNCLEAR:
            return PolicyResult(Decision.REVIEW_REQUIRED, ("PURPOSE_UNCERTAIN",), ())

    if purpose == "professional_profile":
        presentation = suggestion.person_presentation
        if presentation in {
            PersonPresentation.UNCLEAR,
            PersonPresentation.NOT_APPLICABLE,
        }:
            return PolicyResult(Decision.REVIEW_REQUIRED, ("PURPOSE_UNCERTAIN",), ())
        if presentation not in {
            PersonPresentation.ORDINARY_CLOTHING,
            PersonPresentation.WORKWEAR,
        }:
            return PolicyResult(Decision.REJECTED, ("PURPOSE_MISMATCH",), ())

    if purpose == "product":
        if suggestion.solar_visual_cue == SolarVisualCue.UNCLEAR:
            return PolicyResult(Decision.REVIEW_REQUIRED, ("PURPOSE_UNCERTAIN",), ())
        if suggestion.solar_visual_cue == SolarVisualCue.NONE:
            return PolicyResult(Decision.REVIEW_REQUIRED, ("ANALYSIS_INCONSISTENT",), ())

    return PolicyResult(Decision.APPROVED, (), ())
