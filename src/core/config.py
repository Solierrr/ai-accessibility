import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True, slots=True)
class Settings:
    registry_url: str | None = None
    registry_token: str | None = field(default=None, repr=False)
    registry_timeout_seconds: float = 10.0
    registry_max_attempts: int = 3
    jwt_jwks_url: str = "http://localhost:8081/.well-known/jwks.json"
    jwt_issuer: str = "solaria-auth"
    gemini_model: str = "gemini-3.5-flash"
    gemini_timeout_seconds: float = 60.0
    max_concurrent_analyses: int = 4
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
        registry_timeout = float(os.getenv("REGISTRY_TIMEOUT_SECONDS", "10"))
        registry_attempts = int(os.getenv("REGISTRY_MAX_ATTEMPTS", "3"))
        if timeout <= 0 or not 1 <= concurrency <= 32:
            raise ValueError("Configuração de timeout ou concorrência inválida")
        if registry_timeout <= 0 or not 1 <= registry_attempts <= 5:
            raise ValueError(
                "Configuração de timeout ou tentativas do registry inválida"
            )
        return cls(
            registry_url=os.getenv("GOOGLE_REGISTRY_URL") or None,
            registry_token=os.getenv("REGISTRY_CONSUMER_TOKEN") or None,
            registry_timeout_seconds=registry_timeout,
            registry_max_attempts=registry_attempts,
            jwt_jwks_url=os.getenv(
                "JWT_JWK_SET_URI", "http://localhost:8081/.well-known/jwks.json"
            ),
            jwt_issuer=os.getenv("JWT_ISSUER", "solaria-auth"),
            gemini_model=model,
            gemini_timeout_seconds=timeout,
            max_concurrent_analyses=concurrency,
            groq_model=groq_model,
        )

    @property
    def registry_configured(self) -> bool:
        return bool(self.registry_url and self.registry_token)

    @property
    def ready(self) -> bool:
        return bool(self.registry_configured and self.jwt_jwks_url and self.jwt_issuer)
