"""Registro das etapas da análise para o modo debug: só metadados, nunca mídia nem texto."""

import json
import re
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any

from src.providers.capture import CallCapture

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


def llm_call(
    capture: CallCapture,
    index: int,
    completion: str | None,
    *,
    editable: bool = True,
) -> dict[str, Any]:
    record = capture.calls[index]
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
    capture: CallCapture | None, completion: str | None
) -> list[dict[str, Any]]:
    if capture is None:
        return []
    calls = capture.calls
    return [
        llm_call(
            capture,
            index,
            completion if index == len(calls) - 1 else None,
            editable=index == 0,
        )
        for index in range(len(calls))
    ]


def completion_summary(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True)


class StepRecorder:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
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
