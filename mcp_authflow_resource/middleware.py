"""ASGI middleware utilities for MCP resource servers."""

import logging
import os
from collections.abc import Awaitable, Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


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


def create_logging_middleware(
    app: ASGIApp, mask_auth: bool = True
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """Create ASGI middleware to log detailed request information for debugging.

    .. warning:: **CWE-532 — Insertion of Sensitive Information into Log File**

        This middleware logs full request bodies (up to 1000 bytes), all HTTP
        headers, and 400-response bodies at INFO/ERROR level.  When used with
        MCP servers, that includes tool arguments, document content, task
        titles, and any other personal data passed as MCP tool parameters —
        all of which would be forwarded to the configured log sink (e.g. Loki).

        **This middleware must NEVER be left active in production.**

        To prevent accidental production activation this function raises
        ``RuntimeError`` unless the environment variable
        ``MCP_ENABLE_VERBOSE_LOGGING=1`` is explicitly set.  Use only in
        controlled debug environments; remove the variable before deploying.

    Uses raw ASGI interface to avoid interfering with request body or streaming.

    Args:
        app: The ASGI application to wrap
        mask_auth: Whether to mask authorization header values (default: True)

    Returns:
        ASGI middleware function

    Raises:
        RuntimeError: If ``MCP_ENABLE_VERBOSE_LOGGING`` is not set to ``"1"``,
            to guard against accidental production activation.
    """
    if os.environ.get("MCP_ENABLE_VERBOSE_LOGGING") != "1":
        raise RuntimeError(
            "create_logging_middleware() refused: MCP_ENABLE_VERBOSE_LOGGING is not set to '1'. "
            "This middleware logs full request bodies and headers (CWE-532). "
            "Set MCP_ENABLE_VERBOSE_LOGGING=1 only in controlled debug environments, "
            "never in production."
        )

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        # Extract request info from scope
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")
        query_string = scope.get("query_string", b"").decode("utf-8", errors="replace")
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}

        # Log request details
        logger.info("=" * 60)
        logger.info("=== Incoming Request: %s %s ===", method, path)
        if query_string:
            logger.info("Query string: %s", query_string)
        logger.info("Client: %s", scope.get("client"))

        # Log all headers
        logger.info("Headers:")
        for name, value in headers.items():
            # Mask authorization header value for security
            if mask_auth and name.lower() == "authorization":
                logger.info("  %s: Bearer ***", name)
            else:
                logger.info("  %s: %s", name, value)

        # Log specific headers that MCP cares about
        content_type = headers.get("content-type", "NOT SET")
        origin = headers.get("origin", "NOT SET")
        host = headers.get("host", "NOT SET")
        mcp_session = headers.get("mcp-session-id", "NOT SET")
        mcp_protocol = headers.get("mcp-protocol-version", "NOT SET")

        logger.info("Key MCP headers:")
        logger.info("  Content-Type: %s", content_type)
        logger.info("  Origin: %s", origin)
        logger.info("  Host: %s", host)
        logger.info("  Mcp-Session-Id: %s", mcp_session)
        logger.info("  Mcp-Protocol-Version: %s", mcp_protocol)

        # Track response status
        response_status = [None]
        response_headers: list[dict[str, str]] = [{}]

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_status[0] = message.get("status")
                response_headers[0] = {
                    k.decode(): v.decode() for k, v in message.get("headers", [])
                }

                # Log response status
                logger.info("=== Response: %s for %s %s ===", response_status[0], method, path)

                # If it's a 400 error, log more details
                if response_status[0] == 400:
                    logger.error("!!! 400 Bad Request returned !!!")
                    logger.error("Response headers: %s", response_headers[0])

            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and response_status[0] == 400:
                    # Log the response body for 400 errors
                    body_text = body.decode("utf-8", errors="replace")
                    logger.error("400 Response body: %s", body_text)

            await send(message)

        # Log body for POST requests by wrapping receive
        body_logged = [False]

        async def receive_with_logging() -> Message:
            message = await receive()
            if message["type"] == "http.request" and not body_logged[0]:
                body_logged[0] = True
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                if body:
                    body_preview = body[:1000].decode("utf-8", errors="replace")
                    if len(body) > 1000 or more_body:
                        body_preview += "... (truncated/more coming)"
                    logger.info("Request body preview (%s bytes): %s", len(body), body_preview)
            return message

        logger.info("=" * 60)

        if method == "POST":
            await app(scope, receive_with_logging, send_wrapper)
        else:
            await app(scope, receive, send_wrapper)

    return middleware
