"""ASGI middleware utilities for MCP resource servers."""

import logging
import os
from collections.abc import Awaitable, Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Header names (lower-cased) whose values carry credentials and must be masked
# when ``mask_auth`` is enabled. Compared case-insensitively.
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "x-api-key", "proxy-authorization"})


class NormalizePathMiddleware:
    """ASGI middleware to normalize paths so /mcp and /mcp/ work identically.

    Strips trailing slashes from all paths (except root) before routing.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "/")
            # Normalize: strip trailing slash if path is not just "/"
            if path != "/" and path.endswith("/"):
                scope = dict(scope)
                scope["path"] = path.rstrip("/")
        await self.app(scope, receive, send)


# Maximum number of request-body bytes to include in a log preview.
_BODY_PREVIEW_LIMIT = 1000


class VerboseLoggingMiddleware:
    """ASGI middleware that logs detailed request/response info for debugging.

    .. warning:: **CWE-532 — Insertion of Sensitive Information into Log File**

        This middleware logs full request bodies (up to 1000 bytes), all HTTP
        headers, and 400-response bodies at INFO/ERROR level.  When used with
        MCP servers, that includes tool arguments, document content, task
        titles, and any other personal data passed as MCP tool parameters —
        all of which would be forwarded to the configured log sink (e.g. Loki).

        **This middleware must NEVER be left active in production.**

        To prevent accidental production activation, constructing it raises
        ``RuntimeError`` unless the environment variable
        ``MCP_ENABLE_VERBOSE_LOGGING=1`` is explicitly set.  Use only in
        controlled debug environments; remove the variable before deploying.

    Uses the raw ASGI interface to avoid interfering with request body or
    streaming.

    Args:
        app: The ASGI application to wrap.
        mask_auth: Whether to mask credential-bearing header values, i.e. those
            in ``_SENSITIVE_HEADERS`` such as Authorization, Cookie, X-API-Key,
            and Proxy-Authorization (default: True).

    Raises:
        RuntimeError: If ``MCP_ENABLE_VERBOSE_LOGGING`` is not set to ``"1"``,
            to guard against accidental production activation.
    """

    def __init__(self, app: ASGIApp, mask_auth: bool = True) -> None:
        if os.environ.get("MCP_ENABLE_VERBOSE_LOGGING") != "1":
            raise RuntimeError(
                "VerboseLoggingMiddleware refused: MCP_ENABLE_VERBOSE_LOGGING is not set to '1'. "
                "This middleware logs full request bodies and headers (CWE-532). "
                "Set MCP_ENABLE_VERBOSE_LOGGING=1 only in controlled debug environments, "
                "never in production."
            )
        self.app = app
        self.mask_auth = mask_auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")

        self._log_request(scope, method, path)

        send_wrapper = self._make_send_wrapper(send, method, path)
        if method == "POST":
            await self.app(scope, self._make_receive_wrapper(receive), send_wrapper)
        else:
            await self.app(scope, receive, send_wrapper)

    def _log_request(self, scope: Scope, method: str, path: str) -> None:
        """Log the incoming request line, headers, and key MCP headers."""
        query_string = scope.get("query_string", b"").decode("utf-8", errors="replace")
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}

        logger.info("=" * 60)
        logger.info("=== Incoming Request: %s %s ===", method, path)
        if query_string:
            logger.info("Query string: %s", query_string)
        logger.info("Client: %s", scope.get("client"))

        logger.info("Headers:")
        for name, value in headers.items():
            # Mask credential-bearing header values for security.
            if self.mask_auth and name.lower() in _SENSITIVE_HEADERS:
                logger.info("  %s: ***", name)
            else:
                logger.info("  %s: %s", name, value)

        logger.info("Key MCP headers:")
        logger.info("  Content-Type: %s", headers.get("content-type", "NOT SET"))
        logger.info("  Origin: %s", headers.get("origin", "NOT SET"))
        logger.info("  Host: %s", headers.get("host", "NOT SET"))
        logger.info("  Mcp-Session-Id: %s", headers.get("mcp-session-id", "NOT SET"))
        logger.info("  Mcp-Protocol-Version: %s", headers.get("mcp-protocol-version", "NOT SET"))
        logger.info("=" * 60)

    def _make_send_wrapper(self, send: Send, method: str, path: str) -> Send:
        """Wrap ``send`` to log the response status and 400-error details."""
        response_status: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status")
                logger.info("=== Response: %s for %s %s ===", response_status, method, path)

                if response_status == 400:
                    response_headers = {
                        k.decode(): v.decode() for k, v in message.get("headers", [])
                    }
                    logger.error("!!! 400 Bad Request returned !!!")
                    logger.error("Response headers: %s", response_headers)

            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and response_status == 400:
                    body_text = body.decode("utf-8", errors="replace")
                    logger.error("400 Response body: %s", body_text)

            await send(message)

        return send_wrapper

    def _make_receive_wrapper(self, receive: Receive) -> Receive:
        """Wrap ``receive`` to log a preview of the first request body chunk."""
        body_logged = False

        async def receive_with_logging() -> Message:
            nonlocal body_logged
            message = await receive()
            if message["type"] == "http.request" and not body_logged:
                body_logged = True
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                if body:
                    body_preview = body[:_BODY_PREVIEW_LIMIT].decode("utf-8", errors="replace")
                    if len(body) > _BODY_PREVIEW_LIMIT or more_body:
                        body_preview += "... (truncated/more coming)"
                    logger.info("Request body preview (%s bytes): %s", len(body), body_preview)
            return message

        return receive_with_logging


def create_logging_middleware(
    app: ASGIApp, mask_auth: bool = True
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """Create ASGI middleware to log detailed request information for debugging.

    Thin factory around :class:`VerboseLoggingMiddleware`; see that class for
    the full behaviour and the **CWE-532** production-safety warning.

    Args:
        app: The ASGI application to wrap.
        mask_auth: Whether to mask credential-bearing header values (default: True).

    Returns:
        A callable ASGI middleware instance.

    Raises:
        RuntimeError: If ``MCP_ENABLE_VERBOSE_LOGGING`` is not set to ``"1"``,
            to guard against accidental production activation.
    """
    return VerboseLoggingMiddleware(app, mask_auth=mask_auth)
