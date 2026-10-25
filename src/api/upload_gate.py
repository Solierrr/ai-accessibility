"""Autenticação e limite bruto do upload antes do parser multipart."""

from collections.abc import Collection, Mapping

from src.api.errors import ApiError, problem_response
from src.api.service_auth import SERVICE_KEY_HEADER, resolve_caller
from src.image.validation import MAX_UPLOAD_BYTES

MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 256 * 1024  # margem para campos multipart


async def _too_large(scope, receive, send) -> None:
    await problem_response(
        scope["path"], 413, "PAYLOAD_TOO_LARGE", "Upload excede o limite."
    )(scope, receive, send)


class UploadGate:
    def __init__(
        self,
        app,
        *,
        service_keys: Mapping[str, str],
        callers: Collection[str],
    ) -> None:
        self.app = app
        self.service_keys = service_keys
        self.callers = callers

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] != "/v1/images/analyze":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        key = headers.get(SERVICE_KEY_HEADER.lower().encode("latin-1"))
        try:
            resolve_caller(
                key.decode("latin-1") if key else None, self.service_keys, self.callers
            )
        except ApiError as exc:
            await problem_response(
                scope["path"], exc.status, exc.error_code, exc.detail
            )(scope, receive, send)
            return

        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = MAX_REQUEST_BYTES + 1
        if declared > MAX_REQUEST_BYTES:
            await _too_large(scope, receive, send)
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
                await _too_large(scope, receive, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay, send)
