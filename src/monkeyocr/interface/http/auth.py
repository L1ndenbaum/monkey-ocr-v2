"""Single-token Bearer authentication for public routes."""

import hmac
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from monkeyocr.interface.http.schemas import ApiEnvelope, InternalCode

BEARER_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9\-._~+/]+=*$")


class BearerTokenVerifier:
    def __init__(self, token: str) -> None:
        if len(token.encode("ascii", errors="ignore")) < 32:
            raise ValueError("Bearer token must contain at least 32 ASCII bytes.")
        if not BEARER_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Bearer token contains characters that are unsafe in an HTTP header.")
        self._token = token

    @classmethod
    def from_file(cls, path: Path) -> "BearerTokenVerifier":
        try:
            raw = path.read_text(encoding="ascii")
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"Bearer token secret is unavailable: {path}") from exc
        if raw != raw.strip() or "\n" in raw or "\r" in raw:
            raise ValueError(
                "Bearer token secret must contain exactly one token without whitespace."
            )
        return cls(raw)

    def accepts(self, authorization: str | None) -> bool:
        if authorization is None:
            return False
        scheme, separator, credentials = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not credentials:
            return False
        return hmac.compare_digest(credentials, self._token)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, verifier: BearerTokenVerifier) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._verifier = verifier

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path.startswith("/api/v1") and not self._verifier.accepts(
            request.headers.get("Authorization")
        ):
            body = ApiEnvelope[None](
                internal_code=InternalCode.AUTHENTICATION_FAILED,
                message="A valid Bearer token is required.",
                data=None,
            )
            return JSONResponse(
                status_code=401,
                content=body.model_dump(mode="json"),
                headers={"WWW-Authenticate": 'Bearer realm="monkeyocr"'},
            )
        return await call_next(request)
