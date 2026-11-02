"""Monta os agentes de visão na ordem dos provedores com chave (o primeiro é o principal)."""

from src.agents.base.vision_agent import VisionAgent, VisionProvider
from src.core.config.settings import Settings
from src.core.llm.keys import key_client, vision_providers
from src.core.llm.llm_gemini import llm_gemini
from src.core.llm.llm_groq import llm_groq


def build_vision_agents(settings: Settings) -> list[VisionProvider]:
    client = key_client(settings.llm_env)
    agents: list[VisionProvider] = []
    for provider in vision_providers(settings.llm_env):
        if provider == "gemini":
            agents.append(
                VisionAgent(
                    llm_gemini(settings, client),
                    model_version=f"gemini/{settings.gemini_model}",
                    backend="gemini",
                )
            )
        else:
            agents.append(
                VisionAgent(
                    llm_groq(settings, client),
                    model_version=f"groq/{settings.groq_model}",
                    backend="groq",
                )
            )
    return agents
