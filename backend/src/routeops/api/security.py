"""Local browser write boundary and bounded non-upload bodies; not authentication."""

from fastapi.exceptions import RequestValidationError
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOCAL_UI_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def browser_write_origins(cors_origins: tuple[str, ...]) -> tuple[str, ...]:
    # Same-origin Vite proxy requests need no cross-origin read permission. Keep
    # both documented local UI URLs usable with the historical localhost-only .env.
    return tuple(dict.fromkeys((*LOCAL_UI_ORIGINS, *cors_origins)))


def request_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(
        {
            "code": "REQUEST_VALIDATION_FAILED",
            "detail": "Request did not satisfy the input contract.",
            "errors": [{"loc": list(item["loc"]), "type": item["type"]} for item in exc.errors()],
        },
        status_code=422,
    )


class LocalRequestGuard:
    def __init__(self, app: ASGIApp, *, origins: tuple[str, ...], max_json_bytes: int) -> None:
        self.app = app
        self.origins = frozenset(origins)
        self.max_json_bytes = max_json_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            origins = headers.getlist("origin")
            if (
                len(origins) > 1
                or (origins and origins[0] not in self.origins)
                or (not origins and headers.get("sec-fetch-site") == "cross-site")
            ):
                await JSONResponse({"code": "ORIGIN_NOT_ALLOWED"}, status_code=403)(
                    scope, receive, send
                )
                return
            # The private upload receiver owns its stricter streaming/file limits.
            upload = scope["path"].endswith("/imports") and scope["method"] == "POST"
            if not upload:
                lengths = headers.getlist("content-length")
                if lengths and (
                    len(lengths) != 1
                    or len(lengths[0]) > 12
                    or not lengths[0].isascii()
                    or not lengths[0].isdecimal()
                ):
                    await JSONResponse({"code": "REQUEST_LENGTH_INVALID"}, status_code=400)(
                        scope, receive, send
                    )
                    return
                if lengths and int(lengths[0]) > self.max_json_bytes:
                    await self._too_large(scope, receive, send)
                    return
                # Buffer only a bounded JSON body, before parsing or any side effect.
                messages: list[Message] = []
                size = 0
                while True:
                    message = await receive()
                    size += len(message.get("body", b""))
                    if size > self.max_json_bytes:
                        await self._too_large(scope, receive, send)
                        return
                    messages.append(message)
                    if message["type"] == "http.disconnect" or not message.get("more_body", False):
                        break
                iterator = iter(messages)

                async def replay() -> Message:
                    message = next(iterator, None)
                    return message if message is not None else await receive()

                await self.app(scope, replay, send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse({"code": "REQUEST_BODY_LIMIT"}, status_code=413)(scope, receive, send)
