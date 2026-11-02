"""Registro das etapas da análise para o modo debug: só metadados, nunca mídia nem texto."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage

IMAGE_OMITTED = "[imagem omitida]"

_COUNTRY_CODE = r"(?:\+?55\s?)?"
_AREA_CODE = r"(?:\(?\d{2}\)?\s?)?"
_PHONE_NUMBER = r"(?:9\s?)?\d{4}[-\s]?\d{4}\b"
_PII = (
    ("CNPJ", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
    ("CPF", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")),
    ("EMAIL", re.compile(r"[\w.+-]{1,64}@[\w-]{1,255}\.[\w.-]{1,255}")),
    ("TELEFONE", re.compile(_COUNTRY_CODE + _AREA_CODE + _PHONE_NUMBER)),
)


def mask_pii(text: str) -> str:
    for kind, pattern in _PII:
        text = pattern.sub(f"[{kind}]", text)
    return text


def clean_prompt(prompt: str) -> tuple[str, bool]:
    text = mask_pii(prompt)
    return text, text == prompt


@dataclass
class LlmCallRecord:
    prompt: str
    model: str
    started: float = field(default_factory=perf_counter)
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None

    def done(self, *, tokens_in: object = None, tokens_out: object = None) -> None:
        self.latency_ms = round((perf_counter() - self.started) * 1000, 1)
        self.tokens_in = tokens_in if isinstance(tokens_in, int) else None
        self.tokens_out = tokens_out if isinstance(tokens_out, int) else None

    def fail(self, exc: BaseException) -> None:
        self.latency_ms = round((perf_counter() - self.started) * 1000, 1)
        status = getattr(exc, "status_code", None)
        self.error = type(exc).__name__ + (f" (HTTP {status})" if status else "")


class LlmCallCapture(AsyncCallbackHandler):
    """Callback do LangChain: guarda o prompt de sistema, os tokens e a latência de cada chamada."""

    def __init__(self) -> None:
        self.calls: list[LlmCallRecord] = []
        self._open: dict[UUID, LlmCallRecord] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        system = next(
            (
                str(message.content)
                for message in messages[0]
                if message.type == "system"
            ),
            "",
        )
        record = LlmCallRecord(prompt=system, model="")
        self._open[run_id] = record
        self.calls.append(record)

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        record = self._open.pop(run_id, None)
        if record is None:
            return
        message = getattr(response.generations[0][0], "message", None)
        usage = getattr(message, "usage_metadata", None) or {}
        record.done(
            tokens_in=usage.get("input_tokens"), tokens_out=usage.get("output_tokens")
        )

    async def on_llm_error(
        self, error: BaseException, *, run_id: UUID, **kwargs: Any
    ) -> None:
        record = self._open.pop(run_id, None)
        if record is not None:
            record.fail(error)

    def take(self, model: str) -> list[LlmCallRecord]:
        """Entrega as chamadas feitas desde a última retirada, já marcadas com o modelo."""
        calls, self.calls = self.calls, []
        for record in calls:
            record.model = model
        return calls


def llm_call(
    record: LlmCallRecord,
    completion: str | None,
    *,
    editable: bool = True,
) -> dict[str, Any]:
    text, complete = clean_prompt(record.prompt)
    editable = editable and complete
    return {
        "model": record.model,
        "prompt": [
            {"type": "system", "content": text, "editable": editable},
            {"type": "human", "content": IMAGE_OMITTED, "editable": False},
        ],
        "prompt_complete": complete,
        "completion": None if record.error else completion,
        "tokens_in": record.tokens_in,
        "tokens_out": record.tokens_out,
        "latency_ms": record.latency_ms,
        "error": record.error,
    }


def llm_calls(
    calls: list[LlmCallRecord], completion: str | None
) -> list[dict[str, Any]]:
    return [
        llm_call(
            record,
            completion if index == len(calls) - 1 else None,
            editable=index == 0,
        )
        for index, record in enumerate(calls)
    ]


def completion_summary(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True)


class StepRecorder:
    """Etapas da análise para o debug; `capture` vai nos callbacks do grafo para ler as chamadas."""

    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.capture = LlmCallCapture()
        self._started = self._mark = perf_counter()

    def add(
        self,
        stage: str,
        outcome: str,
        *,
        inputs: dict[str, Any] | None = None,
        llm_calls: list[dict[str, Any]] | None = None,
        **info: Any,
    ) -> None:
        """Registra o fim de uma etapa; o tempo vai do fim da anterior até agora."""
        now = perf_counter()
        duration = now - self._mark
        failed = outcome == "error"
        self.nodes.append(
            {
                "order": len(self.nodes) + 1,
                "node": stage,
                "started_at": (
                    datetime.now(UTC) - timedelta(seconds=duration)
                ).isoformat(),
                "duration_ms": round(duration * 1000, 1),
                "status": "error" if failed else "ok",
                "input": inputs,
                "output": {"outcome": outcome, **info},
                "llm_calls": llm_calls or [],
                "error": _failure_text(info) if failed else None,
            }
        )
        self._mark = now

    def providers(self, stage: str) -> list[str]:
        return [
            node["output"]["provider"]
            for node in self.nodes
            if node["node"] == stage and "provider" in node["output"]
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "nodes": list(self.nodes),
            "llm_calls_outside_nodes": [],
            "truncated": False,
            "duration_ms": round((perf_counter() - self._started) * 1000, 1),
        }


def _failure_text(info: dict[str, Any]) -> str:
    cause = info.get("cause_type") or "ProviderError"
    status = info.get("status_code")
    return f"{cause} (HTTP {status})" if status else str(cause)
