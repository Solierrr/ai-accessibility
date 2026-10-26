"""Registro das etapas da análise para o modo debug: só metadados, nunca mídia nem texto."""

from time import perf_counter
from typing import Any


class StepRecorder:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self._started = self._mark = perf_counter()

    def add(self, stage: str, outcome: str, **info: Any) -> None:
        """Registra o fim de uma etapa; o tempo vai do fim da anterior até agora."""
        now = perf_counter()
        self.steps.append(
            {
                "order": len(self.steps) + 1,
                "stage": stage,
                "outcome": outcome,
                "duration_ms": round((now - self._mark) * 1000, 1),
                **info,
            }
        )
        self._mark = now

    def snapshot(self) -> dict[str, Any]:
        return {
            "steps": list(self.steps),
            "duration_ms": round((perf_counter() - self._started) * 1000, 1),
        }
