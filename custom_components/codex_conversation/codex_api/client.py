"""
CodexClient — high-level async client for the Codex Responses API.

Depends only on ``AbstractAuth`` for authenticated HTTP; never touches raw
tokens or session management directly.

Mirrors ``ResponsesClient`` from codex-api/src/endpoint/responses.rs.
"""

from __future__ import annotations

from typing import AsyncIterator
from urllib.parse import urlencode

from .auth import AbstractAuth
from .errors import CodexApiError, CodexRateLimited, CodexServerOverloaded
from .models import CodexModel, ResponseEvent
from .requests import CodexRequest
from .sse import sse_iter

CODEX_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
CODEX_MODELS_ENDPOINT = "https://chatgpt.com/backend-api/codex/models"
CODEX_CLIENT_VERSION = "1.0.0"

_STREAM_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
}


class CodexClient:
    """
    Async client for the OpenAI Codex ``/responses`` endpoint.

    Requires an ``AbstractAuth`` instance — token refresh and session
    management are delegated entirely to the auth layer.

    Example (standalone, using ``CodexAuth``)::

        async with aiohttp.ClientSession() as session:
            auth = CodexAuth(session, CODEX_ENDPOINT, access_token, account_id)
            client = CodexClient(auth)
            async for event in client.stream(request):
                if isinstance(event, OutputTextDelta):
                    print(event.delta, end="", flush=True)

    Example (Home Assistant, using ``CodexHAAuth`` from oauth.py)::

        auth = CodexHAAuth(ha_session, oauth_session)
        client = CodexClient(auth)
        async for event in client.stream(request):
            ...
    """

    def __init__(self, auth: AbstractAuth) -> None:
        self._auth = auth

    async def stream(self, request: CodexRequest) -> AsyncIterator[ResponseEvent]:
        """Submit *request* and stream back typed ``ResponseEvent`` objects.

        Raises a ``CodexError`` subclass on HTTP-level or fatal API errors.
        """
        resp = await self._auth.request(
            "post",
            headers=_STREAM_HEADERS,
            json=request.to_body(),
        )
        try:
            if resp.status == 401:
                raise CodexApiError(
                    401, "Unauthorized — bearer token expired or invalid"
                )
            if resp.status == 429:
                retry_after: float | None = None
                ra = resp.headers.get("Retry-After")
                if ra and ra.isdigit():
                    retry_after = float(ra)
                raise CodexRateLimited(await resp.text(), retry_after=retry_after)
            if resp.status == 503:
                raise CodexServerOverloaded(await resp.text())
            if resp.status >= 400:
                raise CodexApiError(resp.status, await resp.text())

            async for event in sse_iter(resp):
                yield event
        finally:
            resp.release()

    async def list_models(self) -> list[CodexModel]:
        """Return models available to the authenticated ChatGPT/Codex account."""
        endpoint = f"{CODEX_MODELS_ENDPOINT}?{urlencode({'client_version': CODEX_CLIENT_VERSION})}"
        resp = await self._auth.request(
            "get",
            endpoint=endpoint,
            headers={"Accept": "application/json"},
        )
        try:
            if resp.status == 401:
                raise CodexApiError(
                    401, "Unauthorized — bearer token expired or invalid"
                )
            if resp.status >= 400:
                raise CodexApiError(resp.status, await resp.text())

            data = await resp.json()
            models = data.get("models") if isinstance(data, dict) else None
            if not isinstance(models, list):
                return []
            return [model for item in models if (model := CodexModel.from_dict(item))]
        finally:
            resp.release()
