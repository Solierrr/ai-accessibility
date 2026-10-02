from langgraph.graph import END, StateGraph

from src.agents.base.image_analysis_agent import ImageAnalysisProvider
from src.workflow.edges.routing_edges import after_validation
from src.workflow.nodes.image_nodes import analysis_node, policy_node, validate_node
from src.workflow.state.state import ImageAnalysisState


def build_analysis_graph(
    primary: ImageAnalysisProvider, fallback: ImageAnalysisProvider | None = None
):
    graph = StateGraph(ImageAnalysisState)
    graph.add_node("validate", validate_node)
    graph.add_node("analyze", analysis_node(primary, fallback))
    graph.add_node("policy", policy_node)
    graph.set_entry_point("validate")
    graph.add_conditional_edges(
        "validate", after_validation, {"analyze": "analyze", "end": END}
    )
    graph.add_edge("analyze", "policy")
    graph.add_edge("policy", END)
    return graph.compile()
