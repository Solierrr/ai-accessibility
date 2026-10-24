"""Acesso ao modo debug: JWT de admin (RS256/JWKS do api-auth), nunca o token interno."""

import re

import jwt as pyjwt
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jwt import PyJWKClient

from src.core.config import Settings

# Lista de permitidos: um valor desconhecido (por exemplo "PRD" ou "staging") nega.
# Em qualquer ambiente, incluindo PROD, só responde para os admins de DEBUG_ADMIN_SUBS.
DEBUG_ALLOWED_ENVIRONMENTS = frozenset({"LOCAL", "DEV", "QA", "PROD"})
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_clients: dict[str, PyJWKClient] = {}


def debug_admin_subs(settings: Settings) -> set[str]:
    subs = {part.strip().lower() for part in settings.debug_admin_subs.split(",")}
    return {sub for sub in subs if _UUID.match(sub)}


def debug_enabled(settings: Settings) -> bool:
    """A rota só existe em ambiente permitido e com ao menos um admin configurado."""
    return settings.environment.strip().upper() in DEBUG_ALLOWED_ENVIRONMENTS and bool(
        debug_admin_subs(settings)
    )


def decode_user_id(token: str, *, jwks_url: str, issuer: str) -> str | None:
    try:
        client = _clients.setdefault(jwks_url, PyJWKClient(jwks_url))
        key = client.get_signing_key_from_jwt(token)
        return pyjwt.decode(token, key.key, algorithms=["RS256"], issuer=issuer).get(
            "sub"
        )
    except Exception:  # noqa: BLE001 - qualquer falha nega o acesso
        return None


def authorize_debug(
    settings: Settings, credentials: HTTPAuthorizationCredentials | None
) -> str:
    """Devolve o `sub` de um admin; falha fechado em qualquer dúvida."""
    if not debug_enabled(settings):
        raise HTTPException(status_code=404, detail="Not Found")
    if credentials is None:
        raise HTTPException(
            status_code=401, detail="Authorization header deve ser 'Bearer <token>'"
        )
    sub = None
    if settings.jwt_jwks_url and settings.jwt_issuer:
        sub = decode_user_id(
            credentials.credentials,
            jwks_url=settings.jwt_jwks_url,
            issuer=settings.jwt_issuer,
        )
    if not sub:
        raise HTTPException(status_code=401, detail="Token inválido")
    if sub.lower() not in debug_admin_subs(settings):
        raise HTTPException(status_code=403, detail="Sem permissão para o modo debug")
    return sub.lower()
