from dataclasses import dataclass, field
from time import perf_counter


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

    def fail(self, exc: Exception) -> None:
        self.latency_ms = round((perf_counter() - self.started) * 1000, 1)
        status = getattr(exc, "status_code", None)
        self.error = type(exc).__name__ + (f" (HTTP {status})" if status else "")


@dataclass
class CallCapture:
    calls: list[LlmCallRecord] = field(default_factory=list)

    def begin(self, prompt: str, model: str) -> LlmCallRecord:
        record = LlmCallRecord(prompt=prompt, model=model)
        self.calls.append(record)
        return record
