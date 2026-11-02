"""Autenticação entre serviços por X-Service-Key, uma chave por serviço chamador."""

import hmac
from collections.abc import Collection, Mapping

from fastapi.security import APIKeyHeader

from src.api.errors import ApiError

SERVICE_KEY_HEADER = "X-Service-Key"

service_key_header = APIKeyHeader(
    name=SERVICE_KEY_HEADER,
    scheme_name="serviceKey",
    auto_error=False,
    description="Chave do serviço chamador (SERVICE_KEY dele).",
)


def resolve_caller(
    key: str | None, service_keys: Mapping[str, str], callers: Collection[str]
) -> str:
    if key:
        for caller, expected in service_keys.items():
            if expected and hmac.compare_digest(key.encode(), expected.encode()):
                if caller in callers:
                    return caller
                raise ApiError(
                    403, "FORBIDDEN", "Serviço chamador não autorizado nesta rota."
                )
    raise ApiError(401, "UNAUTHENTICATED", "Chave de serviço ausente ou inválida.")
