from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    NONE = "none"
    SUSPECTED = "suspected"
    EXPLICIT = "explicit"


class Context(StrEnum):
    ORDINARY = "ordinary"
    DOCUMENTARY = "documentary"
    EDUCATIONAL = "educational"
    UNCERTAIN = "uncertain"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PurposeEvidence(StrEnum):
    COMPANY_BRANDING = "company_branding"
    COMPANY_FACILITY = "company_facility"
    PERSON_PORTRAIT = "person_portrait"
    PHOTOVOLTAIC_PRODUCT = "photovoltaic_product"
    SOLAR_RELATED_PRODUCT = "solar_related_product"
    UNRELATED = "unrelated"
    UNCLEAR = "unclear"


class PersonPresentation(StrEnum):
    ORDINARY_CLOTHING = "ordinary_clothing"
    WORKWEAR = "workwear"
    BARE_TORSO_OR_UNDERWEAR = "bare_torso_or_underwear"
    UNCLEAR = "unclear"
    NOT_APPLICABLE = "not_applicable"


class SceneContext(StrEnum):
    BUSINESS_PREMISES = "business_premises"
    DOMESTIC_OR_LEISURE = "domestic_or_leisure"
    NEUTRAL = "neutral"
    UNCLEAR = "unclear"


class SolarVisualCue(StrEnum):
    PANEL = "panel"
    INVERTER = "inverter"
    MOUNTING = "mounting"
    PV_ELECTRICAL_COMPONENT = "pv_electrical_component"
    OTHER_CLEAR_PV_EQUIPMENT = "other_clear_pv_equipment"
    NONE = "none"
    UNCLEAR = "unclear"


class VisionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sexual_content: Severity
    graphic_violence: Severity
    hateful_content: Severity
    offensive_text: Severity
    context: Context
    confidence: Confidence
    prompt_injection: bool
    uncertain: bool


class CaptionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alt_text: str
    uncertain: bool


class ImageAnalysisSuggestion(VisionAssessment):
    """Uma resposta do modelo contém moderação e legenda; política decide o destino."""

    is_safe: bool = Field(
        description="Segurança do conteúdo, independente da finalidade do upload"
    )
    purpose_evidence: PurposeEvidence = Field(
        description="Elemento visual principal que relaciona a imagem à finalidade informada"
    )
    person_presentation: PersonPresentation = Field(
        description="Vestimenta visível no retrato; não avalia beleza, corpo ou identidade"
    )
    scene_context: SceneContext = Field(
        description="Contexto visível do ambiente; não comprova vínculo com uma empresa"
    )
    solar_visual_cue: SolarVisualCue = Field(
        description="Indício visual específico de equipamento fotovoltaico"
    )
    motivo_bloqueio: str | None = Field(
        default=None,
        description="Categoria interna de risco se is_safe for falso; nunca expor ao usuário",
    )
    legenda_acessivel: str | None = Field(
        default=None,
        description="Texto alternativo em pt-BR somente se is_safe for verdadeiro",
    )

    def assessment(self) -> VisionAssessment:
        return VisionAssessment.model_validate(
            self.model_dump(include=set(VisionAssessment.model_fields))
        )
