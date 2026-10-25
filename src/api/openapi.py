"""Contrato OpenAPI no padrão da organização, gerado do código."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from src.api.errors import PROBLEM_MEDIA_TYPE, ProblemDetail

TITLE = "Solaria — ai-accessibility"
DESCRIPTION = (
    "Analisa as imagens enviadas à plataforma antes da publicação: verifica se o "
    "conteúdo é adequado e, quando a imagem é aprovada, gera uma descrição curta para "
    "leitores de tela. Não armazena nem publica imagens. Atende só outros serviços: "
    "autentique com a chave de serviço no cabeçalho X-Service-Key."
)
CONTACT = {"name": "Solaria", "url": "https://github.com/Solierrr"}
LICENSE = {
    "name": "CC0-1.0",
    "url": "https://creativecommons.org/publicdomain/zero/1.0/",
}
SERVERS = [
    {"url": "https://ai-accessibility.onrender.com", "description": "QA"},
    {"url": "http://localhost:8080", "description": "Local"},
]
TAGS = [
    {
        "name": "Análise de imagens",
        "description": "Moderação e texto alternativo de imagens.",
    },
    {"name": "Saúde", "description": "Vivo e pronto do serviço."},
]
SWAGGER_UI_PARAMETERS = {
    "persistAuthorization": True,
    "displayRequestDuration": True,
    "tryItOutEnabled": True,
    "tagsSorter": "alpha",
    "operationsSorter": "alpha",
}
HEALTH_PATHS = ("/health", "/ready")
FRAMEWORK_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")


def _problem_schemas() -> dict[str, dict]:
    schema = ProblemDetail.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    definitions = schema.pop("$defs", {})
    return {ProblemDetail.__name__: schema, **definitions}


def build_openapi(app: FastAPI, include_internal: bool) -> dict:
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        servers=app.servers,
        contact=app.contact,
        license_info=app.license_info,
    )
    schema["security"] = [{"serviceKey": []}]
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    for name in FRAMEWORK_VALIDATION_SCHEMAS:
        schemas.pop(name, None)
    schemas.update(_problem_schemas())
    problem = {
        PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetail"}}
    }
    for path, item in schema["paths"].items():
        for operation in item.values():
            responses = operation["responses"]
            responses.setdefault("500", {"description": "Erro interno inesperado"})
            for code, response in responses.items():
                if code[0] in "45" and not (path in HEALTH_PATHS and code == "503"):
                    response["content"] = problem
    if not include_internal:
        schema["paths"] = {
            path: item
            for path, item in schema["paths"].items()
            if not path.startswith("/internal/")
        }
        used = {
            tag
            for item in schema["paths"].values()
            for operation in item.values()
            for tag in operation.get("tags", [])
        }
        schema["tags"] = [tag for tag in schema.get("tags", []) if tag["name"] in used]
    return schema


def install_openapi(app: FastAPI) -> None:
    def public_openapi() -> dict:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app, include_internal=False)
        return app.openapi_schema

    app.openapi = public_openapi
