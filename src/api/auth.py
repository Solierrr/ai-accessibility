import asyncio
import json
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt


class InvalidAccessToken(Exception):
    pass


class AuthKeysUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AccessIdentity:
    user_id: UUID
    session_id: UUID


class ApiAuthTokenVerifier:
    def __init__(self, jwks_url: str, issuer: str, *, cache_seconds: int = 300) -> None:
        parsed = urlsplit(jwks_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("JWT_JWK_SET_URI inválida")
        if not issuer.strip():
            raise ValueError("JWT_ISSUER não configurado")
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.cache_seconds = cache_seconds
        self._keys: dict[str, jwt.PyJWK] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _fetch_keys(self) -> dict[str, jwt.PyJWK]:
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                async with client.stream("GET", self.jwks_url) as response:
                    response.raise_for_status()
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 65_536:
                            raise ValueError("JWKS excede limite de tamanho")
                        chunks.append(chunk)
            document = json.loads(b"".join(chunks))
            entries = document["keys"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
                raise ValueError("JWKS inválido")
            keys: dict[str, jwt.PyJWK] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("JWKS inválido")
                if (
                    entry.get("kty") != "RSA"
                    or entry.get("alg", "RS256") != "RS256"
                    or entry.get("use", "sig") != "sig"
                ):
                    continue
                kid = entry.get("kid")
                if not isinstance(kid, str) or not kid or kid in keys:
                    raise ValueError("JWKS inválido")
                keys[kid] = jwt.PyJWK.from_dict(entry, algorithm="RS256")
            if not keys:
                raise ValueError("JWKS sem chaves RS256")
            return keys
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError,
                ValueError, jwt.PyJWTError) as exc:
            raise AuthKeysUnavailable from exc

    async def _key_for(self, kid: str) -> jwt.PyJWK:
        if monotonic() < self._expires_at and kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            if monotonic() >= self._expires_at or kid not in self._keys:
                self._keys = await self._fetch_keys()
                self._expires_at = monotonic() + self.cache_seconds
            key = self._keys.get(kid)
            if key is None:
                raise InvalidAccessToken
            return key

    async def verify(self, token: str) -> AccessIdentity:
        if not token or len(token) > 8192:
            raise InvalidAccessToken
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken from exc
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
            raise InvalidAccessToken
        key = await self._key_for(kid)
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                options={
                    "require": ["exp", "iat", "iss", "sub", "sid", "token_type"],
                    "verify_aud": False,
                },
            )
            if claims["token_type"] != "access":
                raise InvalidAccessToken
            return AccessIdentity(
                user_id=UUID(claims["sub"]),
                session_id=UUID(claims["sid"]),
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise InvalidAccessToken from exc
