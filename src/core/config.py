"""Configuração lida em cada inicialização; segredos nunca entram no código."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True, slots=True)
class Settings:
    google_api_key: str | None
    internal_api_token: str | None
    gemini_model: str = "gemini-3.5-flash"
    gemini_timeout_seconds: float = 60.0
    max_concurrent_analyses: int = 4
    groq_api_key: str | None = None
    groq_model: str = "qwen/qwen3.8-27b"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        if not _MODEL_PATTERN.fullmatch(model):
            raise ValueError("GEMINI_MODEL contém caracteres inválidos")
        groq_model = os.getenv("GROQ_MODEL") or "qwen/qwen3.8-27b"
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", groq_model):
            raise ValueError("GROQ_MODEL contém caracteres inválidos")
        timeout = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "60"))
        concurrency = int(os.getenv("MAX_CONCURRENT_ANALYSES", "4"))
        if timeout <= 0 or not 1 <= concurrency <= 32:
            raise ValueError("Configuração de timeout ou concorrência inválida")
        return cls(
            google_api_key=(
                os.getenv("GEMINI_AI_ACCESSIBILITY_KEY")
                or os.getenv("GOOGLE_API_KEY")
                or None
            ),
            internal_api_token=os.getenv("INTERNAL_API_TOKEN") or None,
            gemini_model=model,
            gemini_timeout_seconds=timeout,
            max_concurrent_analyses=concurrency,
            groq_api_key=os.getenv("GROQ_AI_ACCESSIBILITY_KEY") or None,
            groq_model=groq_model,
        )

    @property
    def ready(self) -> bool:
        return bool((self.google_api_key or self.groq_api_key) and self.internal_api_token)
