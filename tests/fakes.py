"""Modelos falsos do LangChain para os testes: sem rede e sem chave de IA."""

import contextlib
import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig, RunnableLambda
from pydantic import Field, ValidationError

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class _EchoModel(BaseChatModel):
    """Responde 'ok' com tokens fixos, ou levanta o erro configurado."""

    error: Any = None

    @property
    def _llm_type(self) -> str:
        return "echo"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.error is not None:
            raise self.error
        message = AIMessage(content="ok", usage_metadata=USAGE)
        return ChatResult(generations=[ChatGeneration(message=message)])


async def simulate_llm_call(
    config: RunnableConfig | None, prompt: str, error: BaseException | None = None
) -> None:
    """Faz uma chamada de verdade a um modelo falso, para os callbacks do grafo a registrarem."""
    # o chamador levanta o seu próprio erro
    with contextlib.suppress(Exception):
        await _EchoModel(error=error).ainvoke(
            [SystemMessage(content=prompt), HumanMessage(content="[imagem]")], config
        )


class ScriptedModel(BaseChatModel):
    """Entrega respostas roteirizadas à saída estruturada, como um provedor real faria.

    Cada item de `replies` é um dict (o JSON do modelo), uma tupla (dict, metadados da
    resposta) ou uma exceção, que é levantada na chamada."""

    replies: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise NotImplementedError

    def with_structured_output(self, schema, **kwargs):
        async def run(messages, config: RunnableConfig | None = None):
            self.seen.append(messages)
            reply = self.replies.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            payload, metadata = (
                reply
                if isinstance(reply, tuple)
                else (reply, {"finish_reason": "STOP"})
            )
            raw = AIMessage(
                content=json.dumps(payload),
                response_metadata=metadata,
                usage_metadata=USAGE,
            )
            try:
                parsed, error = schema.model_validate(payload), None
            except ValidationError as exc:
                parsed, error = None, exc
            return {"raw": raw, "parsed": parsed, "parsing_error": error}

        return RunnableLambda(run)
