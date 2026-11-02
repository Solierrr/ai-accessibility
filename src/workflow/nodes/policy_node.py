from langchain_core.runnables import RunnableConfig

from src.moderation.policy import POLICY_VERSION, Decision, decide
from src.workflow.nodes.context import tracker_of
from src.workflow.state.state import ImageAnalysisState


async def policy_node(state: ImageAnalysisState, config: RunnableConfig) -> dict:
    """Decisão determinística sobre os sinais; o modelo nunca decide a publicação."""
    observation = state["observation"]
    policy = decide(observation.assessment, safety_flagged=observation.safety_flagged)
    tracker = tracker_of(config)
    if tracker:
        tracker.add(
            "policy",
            policy.decision.value,
            policy_version=POLICY_VERSION,
            reason_codes=list(policy.reason_codes),
            categories=list(policy.categories),
        )
    if policy.decision == Decision.APPROVED:
        # aprovada segue para a legenda; o desfecho final sai da última etapa
        return {}
    return {
        "decision": policy.decision,
        "reason_codes": policy.reason_codes,
        "categories": policy.categories,
    }
