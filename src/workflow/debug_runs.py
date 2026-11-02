import time
from dataclasses import dataclass, field
from uuid import uuid4

MAX_RUNS_PER_SUB = 10
MAX_AGE_SECONDS = 15 * 60
MAX_BYTES = 64 * 1024 * 1024


@dataclass
class StoredRun:
    sub: str
    content: bytes
    purpose: str
    max_alt_chars: int
    context_title: str
    attempts: dict[str, list[str]]
    run_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: float = field(default_factory=time.time)


class DebugRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, StoredRun] = {}

    def _purge(self) -> None:
        limit = time.time() - MAX_AGE_SECONDS
        for run_id in [i for i, run in self._runs.items() if run.created_at < limit]:
            del self._runs[run_id]

    def add(self, run: StoredRun) -> str:
        self._purge()
        self._runs[run.run_id] = run
        while True:
            mine = [r for r in self._runs.values() if r.sub == run.sub]
            too_many = len(mine) > MAX_RUNS_PER_SUB
            too_big = sum(len(r.content) for r in self._runs.values()) > MAX_BYTES
            if not (too_many or too_big) or len(self._runs) <= 1:
                break
            pool = mine if too_many else list(self._runs.values())
            oldest = min(pool, key=lambda r: r.created_at)
            if oldest.run_id == run.run_id:
                break
            del self._runs[oldest.run_id]
        return run.run_id

    def get(self, sub: str, run_id: str) -> StoredRun | None:
        self._purge()
        run = self._runs.get(run_id)
        return run if run is not None and run.sub == sub else None
