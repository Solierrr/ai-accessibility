from ai_lib.llm import EnvKeyClient, get_chat_model

from src.core.config.settings import Settings


def llm_groq(settings: Settings, client: EnvKeyClient):
    return get_chat_model(
        "groq",
        model=settings.groq_model,
        temperature=0.2,
        purpose="vision",
        client=client,
        timeout=settings.gemini_timeout_seconds,
        max_tokens=4096,
    )
