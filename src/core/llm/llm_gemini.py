from ai_lib.llm import EnvKeyClient, get_chat_model
from langchain_google_genai import HarmBlockThreshold, HarmCategory

from src.core.config.settings import Settings

_BLOCK = HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE


def llm_gemini(settings: Settings, client: EnvKeyClient):
    model = settings.gemini_model
    thinking = {"thinking_level": "minimal"} if model.startswith("gemini-3.5-") else {}
    return get_chat_model(
        "gemini",
        model=model,
        temperature=1.0 if model.startswith("gemini-3") else 0.2,
        purpose="vision",
        client=client,
        timeout=settings.gemini_timeout_seconds,
        max_tokens=4096,
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: _BLOCK,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: _BLOCK,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: _BLOCK,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: _BLOCK,
        },
        **thinking,
    )
