from langchain_google_genai import (
    ChatGoogleGenerativeAI,
    HarmBlockThreshold,
    HarmCategory,
)

from src.core.config import Settings


def llm_gemini(settings: Settings) -> ChatGoogleGenerativeAI:
    if not settings.google_api_key:
        raise ValueError("Gemini não configurado")
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        api_key=settings.google_api_key,
        timeout=settings.gemini_timeout_seconds,
        max_retries=1,
        max_tokens=4096,
        temperature=1.0 if settings.gemini_model.startswith("gemini-3") else 0.2,
        **(
            {"thinking_level": "minimal"}
            if settings.gemini_model.startswith("gemini-3.5-")
            else {}
        ),
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        },
    )
