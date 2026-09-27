"""Sinais estruturados da análise visual. O modelo não decide a publicação."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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
