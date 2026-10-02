from src.workflow.state.state import ImageAnalysisState


def after_validation(state: ImageAnalysisState) -> str:
    return "end" if state.get("decision") else "analyze"
