from langchain_groq import ChatGroq

from src.core.config import Settings


def llm_groq(settings: Settings) -> ChatGroq:
    if not settings.groq_api_key:
        raise ValueError("GroqCloud não configurado")
    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        timeout=settings.gemini_timeout_seconds,
        max_retries=1,
        max_tokens=1024,
        temperature=0.2,
        **(
            {"reasoning_effort": "none"}
            if settings.groq_model == "qwen/qwen3.8-27b"
            else {}
        ),
    )
