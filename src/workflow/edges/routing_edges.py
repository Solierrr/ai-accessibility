from src.workflow.state.state import ImageAnalysisState

CONTINUE = "continue"
FINISH = "finish"


def route(state: ImageAnalysisState) -> str:
    """Segue para a próxima etapa enquanto nenhuma etapa fechou o desfecho."""
    return FINISH if state.get("decision") is not None else CONTINUE
