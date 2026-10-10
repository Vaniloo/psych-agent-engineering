"""Authenticate internal HTTP surfaces before accepting caller-owned identities."""

import hmac

from starlette.responses import JSONResponse


class InternalAuthMiddleware:
    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] not in {"/health", "/metrics"}:
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"x-internal-service-token", b"")
            if not self.token or not hmac.compare_digest(supplied, self.token.encode()):
                await JSONResponse({"detail": "invalid internal credentials"}, status_code=401)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)
