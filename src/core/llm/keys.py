"""Chaves de LLM lidas do ambiente (Infisical): uma ou mais por provedor, em rodízio."""

import os
from collections.abc import Mapping

from ai_lib.llm import EnvKeyClient, configured_providers
from ai_lib.registry import Provider
from dotenv import load_dotenv

# Nomes que este serviço já aceitou antes da base comum; continuam valendo.
LEGACY_NAMES: dict[str, tuple[str, ...]] = {
    "GEMINI_API_KEY": ("GEMINI_AI_ACCESSIBILITY_KEY", "GOOGLE_API_KEY"),
    "GROQ_API_KEY": ("GROQ_AI_ACCESSIBILITY_KEY",),
}
# Só estes provedores analisam imagem aqui.
VISION_PROVIDERS: tuple[Provider, ...] = ("gemini", "groq")


def llm_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Ambiente com os nomes antigos das chaves copiados para os nomes da base comum."""
    if env is None:
        # Expõe o .env local em os.environ; em produção as variáveis já vêm do ambiente.
        load_dotenv(override=False)
    merged = dict(os.environ if env is None else env)
    for name, aliases in LEGACY_NAMES.items():
        if not merged.get(name):
            for alias in aliases:
                if merged.get(alias):
                    merged[name] = merged[alias]
                    break
    return merged


def key_client(env: Mapping[str, str] | None = None) -> EnvKeyClient:
    return EnvKeyClient(llm_environment(env))


def vision_providers(env: Mapping[str, str] | None = None) -> list[Provider]:
    """Provedores de visão com chave, na ordem de LLM_PROVIDER_ORDER (padrão: gemini, groq)."""
    source = llm_environment(env)
    configured = configured_providers(source)
    raw = (source.get("LLM_PROVIDER_ORDER") or "").strip()
    order = [item.strip().lower() for item in raw.split(",") if item.strip()] or list(
        VISION_PROVIDERS
    )
    return [
        provider
        for provider in order
        if provider in VISION_PROVIDERS and provider in configured
    ]
