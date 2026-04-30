"""Tests for NormalizePathMiddleware and create_logging_middleware."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.types import Receive, Scope, Send

from mcp_authflow_resource.middleware import NormalizePathMiddleware, create_logging_middleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_scope(
    path: str, method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
    }


def _make_non_http_scope(scope_type: str = "websocket") -> dict[str, Any]:
    return {"type": scope_type, "path": "/some/path"}


async def _null_app(scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
    """A trivial ASGI app that does nothing."""


async def _capturing_app(
    captured: list[dict[str, Any]],
) -> Any:  # noqa: ANN401
    """Return an ASGI app that records the scope it received."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
        captured.append(scope)

    return app


# ---------------------------------------------------------------------------
# NormalizePathMiddleware
# ---------------------------------------------------------------------------


class TestNormalizePathMiddleware:
    """Path normalisation for HTTP scopes."""

    async def test_trailing_slash_stripped(self) -> None:
        """A trailing slash is removed so /tools/ becomes /tools."""
        captured: list[dict[str, Any]] = []
        inner = await _capturing_app(captured)
        mw = NormalizePathMiddleware(inner)

        scope = _make_http_scope("/tools/")
        await mw(scope, AsyncMock(), AsyncMock())

        assert captured[0]["path"] == "/tools"

    async def test_root_path_unchanged(self) -> None:
        """The root path / must not be modified."""
        captured: list[dict[str, Any]] = []
        inner = await _capturing_app(captured)
        mw = NormalizePathMiddleware(inner)

        scope = _make_http_scope("/")
        await mw(scope, AsyncMock(), AsyncMock())

        assert captured[0]["path"] == "/"

    async def test_path_without_trailing_slash_unchanged(self) -> None:
        """/tools without a trailing slash passes through unchanged."""
        captured: list[dict[str, Any]] = []
        inner = await _capturing_app(captured)
        mw = NormalizePathMiddleware(inner)

        scope = _make_http_scope("/tools")
        await mw(scope, AsyncMock(), AsyncMock())

        assert captured[0]["path"] == "/tools"

    async def test_multiple_trailing_slashes_stripped(self) -> None:
        """Double (and more) trailing slashes are all stripped."""
        captured: list[dict[str, Any]] = []
        inner = await _capturing_app(captured)
        mw = NormalizePathMiddleware(inner)

        scope = _make_http_scope("/tools//")
        await mw(scope, AsyncMock(), AsyncMock())

        assert captured[0]["path"] == "/tools"

    async def test_non_http_scope_passes_through_unchanged(self) -> None:
        """Non-HTTP scopes (e.g. websocket) are forwarded without modification."""
        captured: list[dict[str, Any]] = []
        inner = await _capturing_app(captured)
        mw = NormalizePathMiddleware(inner)

        scope = _make_non_http_scope("websocket")
        await mw(scope, AsyncMock(), AsyncMock())

        assert captured[0] is scope
        assert captured[0]["path"] == "/some/path"

    async def test_original_scope_not_mutated(self) -> None:
        """The original scope dict must not be mutated; a copy is passed downstream."""
        captured: list[dict[str, Any]] = []
        inner = await _capturing_app(captured)
        mw = NormalizePathMiddleware(inner)

        original_scope = _make_http_scope("/api/")
        await mw(original_scope, AsyncMock(), AsyncMock())

        # The original scope still has the trailing slash.
        assert original_scope["path"] == "/api/"
        # The downstream app received the normalised copy.
        assert captured[0]["path"] == "/api"
        assert captured[0] is not original_scope

    async def test_lifespan_scope_passes_through(self) -> None:
        """ASGI lifespan scope (type='lifespan') is forwarded without path changes."""
        captured: list[dict[str, Any]] = []
        inner = await _capturing_app(captured)
        mw = NormalizePathMiddleware(inner)

        scope: dict[str, Any] = {"type": "lifespan"}
        await mw(scope, AsyncMock(), AsyncMock())

        assert captured[0] is scope


# ---------------------------------------------------------------------------
# create_logging_middleware
# ---------------------------------------------------------------------------


class TestCreateLoggingMiddleware:
    """Logging middleware logs request and response details."""

    async def test_non_http_scope_forwarded_directly(self) -> None:
        """Non-HTTP scopes are passed straight through without logging."""
        inner = AsyncMock()
        mw = create_logging_middleware(inner)

        scope = _make_non_http_scope("lifespan")
        receive = AsyncMock()
        send = AsyncMock()
        await mw(scope, receive, send)

        inner.assert_called_once_with(scope, receive, send)

    async def test_get_request_logs_method_and_path(self, caplog: pytest.LogCaptureFixture) -> None:
        """GET request emits INFO log lines with method and path."""
        inner = AsyncMock()
        mw = create_logging_middleware(inner)

        scope = _make_http_scope("/mcp", method="GET")
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.INFO, logger=log_name):
            await mw(scope, AsyncMock(), AsyncMock())

        messages = " ".join(r.message for r in caplog.records)
        assert "GET" in messages
        assert "/mcp" in messages

    async def test_authorization_header_masked_by_default(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Authorization header value is replaced with '***' when mask_auth=True."""
        inner = AsyncMock()
        mw = create_logging_middleware(inner, mask_auth=True)

        scope = _make_http_scope(
            "/mcp",
            headers=[(b"authorization", b"Bearer secret-token")],
        )
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.INFO, logger=log_name):
            await mw(scope, AsyncMock(), AsyncMock())

        messages = " ".join(r.message for r in caplog.records)
        assert "secret-token" not in messages
        assert "***" in messages

    async def test_authorization_header_not_masked_when_disabled(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Authorization header value appears in logs when mask_auth=False."""
        inner = AsyncMock()
        mw = create_logging_middleware(inner, mask_auth=False)

        scope = _make_http_scope(
            "/mcp",
            headers=[(b"authorization", b"Bearer visible-token")],
        )
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.INFO, logger=log_name):
            await mw(scope, AsyncMock(), AsyncMock())

        messages = " ".join(r.message for r in caplog.records)
        assert "visible-token" in messages

    async def test_response_status_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Response status is captured and logged when app sends http.response.start."""

        async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = create_logging_middleware(inner_app)
        scope = _make_http_scope("/mcp")
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.INFO, logger=log_name):
            await mw(scope, AsyncMock(), AsyncMock())

        messages = " ".join(r.message for r in caplog.records)
        assert "200" in messages

    async def test_400_response_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A 400 response triggers ERROR-level log entries."""

        async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"Bad Request detail"})

        mw = create_logging_middleware(inner_app)
        scope = _make_http_scope("/mcp")
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.DEBUG, logger=log_name):
            await mw(scope, AsyncMock(), AsyncMock())

        error_messages = " ".join(r.message for r in caplog.records if r.levelno >= logging.ERROR)
        assert "400" in error_messages

    async def test_post_request_body_preview_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """POST request body content appears in the log output."""
        body_content = b'{"method": "tools/list"}'

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body_content, "more_body": False}

        async def inner_app(scope: Scope, recv: Receive, send: Send) -> None:
            # Consume the body so the logging wrapper fires.
            await recv()

        mw = create_logging_middleware(inner_app)
        scope = _make_http_scope("/mcp", method="POST")
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.INFO, logger=log_name):
            await mw(scope, receive, AsyncMock())

        messages = " ".join(r.message for r in caplog.records)
        assert "tools/list" in messages

    async def test_query_string_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Non-empty query string appears in log output."""
        inner = AsyncMock()
        mw = create_logging_middleware(inner)

        scope = _make_http_scope("/mcp")
        scope["query_string"] = b"foo=bar"
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.INFO, logger=log_name):
            await mw(scope, AsyncMock(), AsyncMock())

        messages = " ".join(r.message for r in caplog.records)
        assert "foo=bar" in messages

    async def test_post_large_body_preview_truncated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """POST body larger than 1000 bytes logs a truncation notice."""
        large_body = b"x" * 1500

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": large_body, "more_body": False}

        async def inner_app(scope: Scope, recv: Receive, send: Send) -> None:
            await recv()

        mw = create_logging_middleware(inner_app)
        scope = _make_http_scope("/mcp", method="POST")
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.INFO, logger=log_name):
            await mw(scope, receive, AsyncMock())

        messages = " ".join(r.message for r in caplog.records)
        assert "truncated" in messages

    async def test_post_body_with_more_body_flag_truncated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """POST body with more_body=True also logs a truncation notice."""
        partial_body = b'{"partial": "data"}'

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": partial_body, "more_body": True}

        async def inner_app(scope: Scope, recv: Receive, send: Send) -> None:
            await recv()

        mw = create_logging_middleware(inner_app)
        scope = _make_http_scope("/mcp", method="POST")
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.INFO, logger=log_name):
            await mw(scope, receive, AsyncMock())

        messages = " ".join(r.message for r in caplog.records)
        assert "truncated" in messages

    async def test_400_response_body_logged_as_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """400 response body text appears in ERROR-level log output."""

        async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"invalid content-type"})

        mw = create_logging_middleware(inner_app)
        scope = _make_http_scope("/mcp")
        log_name = "mcp_authflow_resource.middleware"

        with caplog.at_level(logging.DEBUG, logger=log_name):
            await mw(scope, AsyncMock(), AsyncMock())

        error_messages = " ".join(r.message for r in caplog.records if r.levelno >= logging.ERROR)
        assert "invalid content-type" in error_messages

    async def test_non_post_request_body_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """GET requests use the original receive callable (no body logging)."""

        async def tracking_receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def inner_app(scope: Scope, recv: Receive, send: Send) -> None:
            # The recv passed to a GET request should be the original, unmodified callable.
            assert recv is tracking_receive

        mw = create_logging_middleware(inner_app)
        scope = _make_http_scope("/mcp", method="GET")

        await mw(scope, tracking_receive, AsyncMock())
