"""Autenticação e limite bruto do upload antes do parser multipart."""

import hmac

from starlette.responses import JSONResponse

from src.image.validation import MAX_UPLOAD_BYTES

MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 256 * 1024  # margem para campos multipart
MAX_STAGE_BYTES = 8 * 1024 * 1024
ANALYZE_PATH = "/v1/images/analyze"
DEBUG_ANALYZE_PATH = "/debug/images/analyze"
DEBUG_STAGE_PATH = "/debug/images/stage"


class UploadGate:
    def __init__(self, app, *, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] not in (
            ANALYZE_PATH,
            DEBUG_ANALYZE_PATH,
            DEBUG_STAGE_PATH,
        ):
            await self.app(scope, receive, send)
            return

        limit = (
            MAX_STAGE_BYTES if scope["path"] == DEBUG_STAGE_PATH else MAX_REQUEST_BYTES
        )

        headers = dict(scope.get("headers", []))

        if scope["path"] == ANALYZE_PATH:
            if not self.token:
                await JSONResponse(
                    {"detail": "Autenticação interna não configurada"}, status_code=503
                )(scope, receive, send)
                return
            expected = b"Bearer " + self.token.encode("utf-8")
            if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
                await JSONResponse(
                    {"detail": "Credencial interna inválida"}, status_code=401
                )(scope, receive, send)
                return

        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1
        if declared > limit:
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
            if total > limit:
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
