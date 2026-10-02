import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from src.api.auth import (
    ApiAuthTokenVerifier,
    AuthKeysUnavailable,
    InvalidAccessToken,
)


class ApiAuthTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(
            cls.private_key.public_key(), as_dict=True
        )
        public_jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
        cls.public_key = jwt.PyJWK.from_dict(public_jwk, algorithm="RS256")

    def verifier(self) -> ApiAuthTokenVerifier:
        verifier = ApiAuthTokenVerifier(
            "http://localhost:8081/.well-known/jwks.json", "solaria-auth"
        )
        verifier._fetch_keys = AsyncMock(return_value={"test-key": self.public_key})
        return verifier

    def token(self, **overrides) -> str:
        now = datetime.now(timezone.utc)
        claims = {
            "iss": "solaria-auth",
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "token_type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=15),
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def test_accepts_api_auth_access_token(self) -> None:
        verifier = self.verifier()
        identity = asyncio.run(verifier.verify(self.token()))
        self.assertIsNotNone(identity.user_id)
        self.assertIsNotNone(identity.session_id)
        self.assertEqual(verifier._fetch_keys.await_count, 1)
        asyncio.run(verifier.verify(self.token()))
        self.assertEqual(verifier._fetch_keys.await_count, 1)

    def test_rejects_expired_wrong_issuer_and_wrong_type(self) -> None:
        now = datetime.now(timezone.utc)
        for token in (
            self.token(exp=now - timedelta(seconds=1)),
            self.token(iss="another-service"),
            self.token(token_type="refresh"),
            self.token(sid="not-a-uuid"),
        ):
            with self.subTest(token=token[:8]), self.assertRaises(InvalidAccessToken):
                asyncio.run(self.verifier().verify(token))

    def test_rejects_tampered_and_unsigned_tokens(self) -> None:
        signed = self.token()
        tampered = signed[:-3] + ("abc" if signed[-3:] != "abc" else "def")
        for token in (
            tampered,
            jwt.encode(
                {"sub": str(uuid4())},
                key="",
                algorithm="none",
                headers={"kid": "test-key"},
            ),
        ):
            with self.assertRaises(InvalidAccessToken):
                asyncio.run(self.verifier().verify(token))

    def test_unavailable_jwks_does_not_accept_token(self) -> None:
        verifier = self.verifier()
        verifier._fetch_keys = AsyncMock(side_effect=AuthKeysUnavailable)
        with self.assertRaises(AuthKeysUnavailable):
            asyncio.run(verifier.verify(self.token()))


if __name__ == "__main__":
    unittest.main()
