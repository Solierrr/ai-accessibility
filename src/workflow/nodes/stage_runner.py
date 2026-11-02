"""Executa uma etapa de modelo com a regra de fallback: só falha técnica troca de provedor."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.agents.base.vision_agent import ProviderBlocked, ProviderError, VisionProvider
from src.workflow.nodes.context import tracker_of
from src.workflow.observability.debug_tracker import llm_calls

logger = logging.getLogger(__name__)

SAFETY_FLAG = "PROVIDER_SAFETY_FLAG"
UNAVAILABLE = "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class StageSuccess[T]:
    agent: VisionProvider
    result: T


@dataclass(frozen=True, slots=True)
class StageFailure:
    reason: str


def log_provider_failure(provider_name: str, stage: str, exc: ProviderError) -> None:
    """Registra somente metadados seguros; nunca imagem, prompt ou resposta bruta."""
    cause_type = type(exc.__cause__).__name__ if exc.__cause__ else "none"
    logger.warning(
        "provider_failure provider=%s stage=%s status=%s cause_type=%s",
        provider_name,
        stage,
        exc.status_code,
        cause_type,
    )


async def run_stage[T](
    stage: str,
    agents: Sequence[VisionProvider],
    call: Callable[[VisionProvider, RunnableConfig], Awaitable[T]],
    config: RunnableConfig,
    *,
    inputs: dict[str, Any],
    summarize: Callable[[T], dict[str, Any]],
    completion: Callable[[T], str],
) -> StageSuccess[T] | StageFailure:
    """Tenta cada agente em ordem. Bloqueio de segurança encerra a etapa sem tentar outro."""
    tracker = tracker_of(config)
    for agent in agents:
        try:
            result = await call(agent, config)
        except ProviderBlocked as exc:
            _note_failure(tracker, stage, agent, exc, inputs, "blocked")
            return StageFailure(SAFETY_FLAG)
        except ProviderError as exc:
            log_provider_failure(agent.model_version, stage, exc)
            _note_failure(tracker, stage, agent, exc, inputs, "error")
            continue
        if tracker is not None:
            tracker.add(
                stage,
                "ok",
                provider=agent.model_version,
                inputs=inputs,
                llm_calls=llm_calls(
                    tracker.capture.take(agent.model_version), completion(result)
                ),
                **summarize(result),
            )
        return StageSuccess(agent, result)
    return StageFailure(UNAVAILABLE)


def _note_failure(
    tracker: Any,
    stage: str,
    agent: VisionProvider,
    exc: ProviderError,
    inputs: dict[str, Any],
    outcome: str,
) -> None:
    if tracker is None:
        return
    tracker.add(
        stage,
        outcome,
        provider=agent.model_version,
        status_code=exc.status_code,
        cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None,
        inputs=inputs,
        llm_calls=llm_calls(tracker.capture.take(agent.model_version), None),
    )
