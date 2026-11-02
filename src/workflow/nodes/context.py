"""Dependências do grafo que não fazem parte do estado: agentes, rastreador e prompts editados."""

from collections.abc import Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.agents.base.vision_agent import VisionProvider
from src.workflow.observability.debug_tracker import StepRecorder


def agents_of(config: RunnableConfig) -> Sequence[VisionProvider]:
    return config["configurable"]["agents"]


def tracker_of(config: RunnableConfig) -> StepRecorder | None:
    return config["configurable"].get("tracker")


def prompt_override(config: RunnableConfig, stage: str) -> str | None:
    overrides: dict[str, Any] = config["configurable"].get("prompt_overrides") or {}
    return overrides.get(stage)
