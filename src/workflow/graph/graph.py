from langgraph.graph import END, StateGraph

from src.workflow.edges.routing_edges import CONTINUE, FINISH, route
from src.workflow.nodes.caption_node import caption_node
from src.workflow.nodes.caption_validation_node import caption_validation_node
from src.workflow.nodes.consolidation_node import consolidation_node
from src.workflow.nodes.moderation_node import moderation_node
from src.workflow.nodes.policy_node import policy_node
from src.workflow.nodes.validation_node import validation_node
from src.workflow.state.state import ImageAnalysisState


def build_analysis_graph():
    """image_validation → moderation → policy → caption → caption_validation → consolidation.

    Qualquer etapa que feche o desfecho (rejeição, revisão) desvia direto para a consolidação.
    O grafo não usa checkpointer: cada requisição é independente e nada é guardado."""
    graph = StateGraph(ImageAnalysisState)
    graph.add_node("image_validation", validation_node)
    graph.add_node("moderation", moderation_node)
    graph.add_node("policy", policy_node)
    graph.add_node("caption", caption_node)
    graph.add_node("caption_validation", caption_validation_node)
    graph.add_node("consolidation", consolidation_node)
    graph.set_entry_point("image_validation")
    for source, target in (
        ("image_validation", "moderation"),
        ("moderation", "policy"),
        ("policy", "caption"),
        ("caption", "caption_validation"),
    ):
        graph.add_conditional_edges(
            source, route, {CONTINUE: target, FINISH: "consolidation"}
        )
    graph.add_edge("caption_validation", "consolidation")
    graph.add_edge("consolidation", END)
    return graph.compile()


analysis_graph = build_analysis_graph()
