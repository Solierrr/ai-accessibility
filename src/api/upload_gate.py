from starlette.responses import JSONResponse

from src.api.auth import ApiAuthTokenVerifier, AuthKeysUnavailable, InvalidAccessToken
from src.image.validation import MAX_UPLOAD_BYTES

MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 256 * 1024  # margem para campos multipart


class UploadGate:
    def __init__(self, app, *, verifier: ApiAuthTokenVerifier) -> None:
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] != "/v1/images/analyze":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"")
        try:
            if not authorization.startswith(b"Bearer "):
                raise InvalidAccessToken
            token = authorization.removeprefix(b"Bearer ").decode("ascii")
            await self.verifier.verify(token)
        except (InvalidAccessToken, UnicodeDecodeError):
            await JSONResponse(
                {"detail": "Access token inválido ou expirado"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        except AuthKeysUnavailable:
            await JSONResponse(
                {"detail": "Não foi possível validar o access token"},
                status_code=503,
            )(scope, receive, send)
            return

        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = MAX_REQUEST_BYTES + 1
        if declared > MAX_REQUEST_BYTES:
            await JSONResponse({"detail": "Upload excede o limite"}, status_code=413)(
                scope, receive, send
            )
            return

        messages = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            total += len(message.get("body", b""))
            if total > MAX_REQUEST_BYTES:
                await JSONResponse(
                    {"detail": "Upload excede o limite"}, status_code=413
                )(scope, receive, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay, send)
