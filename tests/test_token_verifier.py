"""Tests for IntrospectionTokenVerifier — RFC 7662 token introspection."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mcp_authflow_resource.auth.token_verifier import IntrospectionTokenVerifier

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTROSPECTION_URL = "http://localhost:8080/introspect"
SERVER_URL = "https://mcp.example.com"

_ACTIVE_TOKEN_DATA: dict[str, Any] = {
    "active": True,
    "client_id": "test-client",
    "scope": "read write",
    "exp": 9999999999,
    "aud": SERVER_URL,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier(
    introspection_endpoint: str = INTROSPECTION_URL,
    server_url: str = SERVER_URL,
    validate_resource: bool = False,
) -> IntrospectionTokenVerifier:
    return IntrospectionTokenVerifier(
        introspection_endpoint=introspection_endpoint,
        server_url=server_url,
        validate_resource=validate_resource,
    )


def _mock_http_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a mock httpx.Response-like object."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    return response


# ---------------------------------------------------------------------------
# SSRF guard tests
# ---------------------------------------------------------------------------


class TestSSRFGuard:
    """verify_token must reject unsafe introspection endpoint URLs."""

    async def test_unsafe_url_returns_none_without_http_call(self) -> None:
        """An unsafe scheme (e.g. file://) is rejected before any HTTP call."""
        verifier = _make_verifier(introspection_endpoint="file:///etc/passwd")

        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await verifier.verify_token("some-token")

        assert result is None
        mock_client_cls.assert_not_called()

    async def test_external_http_url_returns_none(self) -> None:
        """Plain http:// to an external host is rejected (SSRF guard)."""
        verifier = _make_verifier(introspection_endpoint="http://evil.example.com/introspect")

        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await verifier.verify_token("some-token")

        assert result is None
        mock_client_cls.assert_not_called()

    async def test_https_url_is_accepted(self) -> None:
        """HTTPS endpoints pass the SSRF guard and proceed to HTTP."""
        verifier = _make_verifier(introspection_endpoint="https://auth.example.com/introspect")

        mock_response = _mock_http_response(200, _ACTIVE_TOKEN_DATA)
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("some-token")

        assert result is not None
        mock_post.assert_called_once()

    async def test_localhost_http_url_is_accepted(self) -> None:
        """http://localhost is permitted (allow_localhost=True in the verifier)."""
        verifier = _make_verifier(introspection_endpoint="http://localhost:8080/introspect")

        mock_response = _mock_http_response(200, _ACTIVE_TOKEN_DATA)
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("some-token")

        assert result is not None

    async def test_ssrf_unsafe_url_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """An unsafe URL should produce a WARNING log."""
        verifier = _make_verifier(introspection_endpoint="ftp://evil.example.com/")
        log_name = "mcp_authflow_resource.auth.token_verifier"

        with (
            patch("httpx.AsyncClient"),
            caplog.at_level(logging.WARNING, logger=log_name),
        ):
            await verifier.verify_token("tok")

        assert any("unsafe scheme" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# HTTP response error tests
# ---------------------------------------------------------------------------


class TestHttpErrors:
    """Non-200 responses and network errors should return None."""

    @pytest.mark.parametrize("status_code", [400, 401, 403, 500, 503])
    async def test_non_200_status_returns_none(self, status_code: int) -> None:
        """Any non-200 HTTP status code causes verify_token to return None."""
        verifier = _make_verifier()
        mock_response = _mock_http_response(status_code, {})
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("some-token")

        assert result is None

    async def test_network_exception_returns_none(self) -> None:
        """Network-level exceptions (e.g. ConnectError) are caught and return None."""
        verifier = _make_verifier()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(
                    post=AsyncMock(side_effect=httpx.ConnectError("connection refused"))
                )
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("some-token")

        assert result is None

    async def test_arbitrary_exception_returns_none(self) -> None:
        """Any unexpected exception inside the HTTP block is caught and returns None."""
        verifier = _make_verifier()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(side_effect=RuntimeError("boom")))
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("some-token")

        assert result is None

    async def test_exception_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Caught exceptions should be logged at WARNING level."""
        verifier = _make_verifier()
        log_name = "mcp_authflow_resource.auth.token_verifier"

        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            caplog.at_level(logging.WARNING, logger=log_name),
        ):
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(side_effect=RuntimeError("oops")))
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await verifier.verify_token("tok")

        assert any("introspection failed" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Active / inactive token tests
# ---------------------------------------------------------------------------


class TestTokenActiveFlag:
    """The `active` field in the introspection response controls token validity."""

    async def test_active_false_returns_none(self) -> None:
        """active: false must cause verify_token to return None."""
        verifier = _make_verifier()
        mock_response = _mock_http_response(200, {"active": False})
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("some-token")

        assert result is None

    async def test_active_missing_returns_none(self) -> None:
        """A response with no `active` key (defaults to False) returns None."""
        verifier = _make_verifier()
        mock_response = _mock_http_response(200, {"client_id": "x"})
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("some-token")

        assert result is None

    async def test_active_true_returns_access_token(self) -> None:
        """active: true with full data returns a populated AccessToken."""
        verifier = _make_verifier()
        mock_response = _mock_http_response(200, _ACTIVE_TOKEN_DATA)
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("my-token")

        assert result is not None
        assert result.token == "my-token"
        assert result.client_id == "test-client"
        assert result.scopes == ["read", "write"]
        assert result.expires_at == 9999999999

    async def test_active_true_no_scope_returns_empty_scopes(self) -> None:
        """active: true with no scope field yields an empty scopes list."""
        verifier = _make_verifier()
        token_data = {"active": True, "client_id": "c"}
        mock_response = _mock_http_response(200, token_data)
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("tok")

        assert result is not None
        assert result.scopes == []

    async def test_unknown_client_id_defaults_to_unknown(self) -> None:
        """When client_id is absent, the AccessToken gets 'unknown' as client_id."""
        verifier = _make_verifier()
        token_data = {"active": True}
        mock_response = _mock_http_response(200, token_data)
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("tok")

        assert result is not None
        assert result.client_id == "unknown"


# ---------------------------------------------------------------------------
# Resource validation tests  (validate_resource=True)
# ---------------------------------------------------------------------------


class TestResourceValidation:
    """RFC 8707 resource validation via the `aud` claim."""

    async def _call_verifier_with_data(
        self, token_data: dict[str, Any], validate_resource: bool = True
    ) -> Any:  # noqa: ANN401
        verifier = _make_verifier(validate_resource=validate_resource)
        mock_response = _mock_http_response(200, token_data)
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            return await verifier.verify_token("tok")

    async def test_string_aud_matches_server_url(self) -> None:
        """String `aud` matching the server URL passes resource validation."""
        token_data = {**_ACTIVE_TOKEN_DATA, "aud": SERVER_URL}
        result = await self._call_verifier_with_data(token_data)
        assert result is not None

    async def test_list_aud_containing_server_url_resource_validation_passes(self) -> None:
        """List `aud` containing the server URL passes _validate_resource.

        NOTE: verify_token subsequently passes the list as `resource` to
        AccessToken, which requires a string — Pydantic raises a validation
        error that is caught by the except block and returns None. This test
        documents the current behaviour. The _validate_resource logic itself
        correctly accepts a matching list (tested directly in
        TestValidateResourceMethod).
        """
        token_data = {
            **_ACTIVE_TOKEN_DATA,
            "aud": ["https://other.example.com", SERVER_URL],
        }
        # The list aud passes _validate_resource but AccessToken rejects the
        # list via Pydantic, so verify_token returns None here.
        result = await self._call_verifier_with_data(token_data)
        assert result is None

    async def test_list_aud_not_containing_server_url_returns_none(self) -> None:
        """List `aud` that does not include the server URL fails validation."""
        token_data = {
            **_ACTIVE_TOKEN_DATA,
            "aud": ["https://other.example.com", "https://another.example.com"],
        }
        result = await self._call_verifier_with_data(token_data)
        assert result is None

    async def test_string_aud_mismatch_returns_none(self) -> None:
        """String `aud` that does not match the server URL fails validation."""
        token_data = {**_ACTIVE_TOKEN_DATA, "aud": "https://different.example.com"}
        result = await self._call_verifier_with_data(token_data)
        assert result is None

    async def test_missing_aud_returns_none(self) -> None:
        """Token with no `aud` claim fails resource validation."""
        token_data = {k: v for k, v in _ACTIVE_TOKEN_DATA.items() if k != "aud"}
        result = await self._call_verifier_with_data(token_data)
        assert result is None

    async def test_resource_validation_disabled_skips_aud_check(self) -> None:
        """With validate_resource=False, a mismatched `aud` still returns a token."""
        token_data = {**_ACTIVE_TOKEN_DATA, "aud": "https://different.example.com"}
        result = await self._call_verifier_with_data(token_data, validate_resource=False)
        assert result is not None

    async def test_resource_validation_logs_warning_on_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Resource validation failure should log a WARNING."""
        token_data = {**_ACTIVE_TOKEN_DATA, "aud": "https://different.example.com"}
        log_name = "mcp_authflow_resource.auth.token_verifier"

        verifier = _make_verifier(validate_resource=True)
        mock_response = _mock_http_response(200, token_data)
        mock_post = AsyncMock(return_value=mock_response)

        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            caplog.at_level(logging.WARNING, logger=log_name),
        ):
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await verifier.verify_token("tok")

        assert result is None
        assert any("resource validation failed" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# _validate_resource unit tests
# ---------------------------------------------------------------------------


class TestValidateResourceMethod:
    """Direct unit tests for IntrospectionTokenVerifier._validate_resource."""

    def _make(self, server_url: str = SERVER_URL) -> IntrospectionTokenVerifier:
        return _make_verifier(server_url=server_url, validate_resource=True)

    def test_returns_false_when_server_url_empty(self) -> None:
        """_validate_resource returns False when server_url is empty."""
        verifier = IntrospectionTokenVerifier(
            introspection_endpoint=INTROSPECTION_URL,
            server_url="",
            validate_resource=True,
        )
        # resource_url will be empty/None
        assert verifier._validate_resource({"aud": SERVER_URL}) is False

    def test_list_aud_all_mismatched_returns_false(self) -> None:
        verifier = self._make()
        assert (
            verifier._validate_resource({"aud": ["https://a.example.com", "https://b.example.com"]})
            is False
        )

    def test_list_aud_one_match_returns_true(self) -> None:
        verifier = self._make()
        assert (
            verifier._validate_resource({"aud": ["https://nope.example.com", SERVER_URL]}) is True
        )

    def test_empty_aud_list_returns_false(self) -> None:
        verifier = self._make()
        assert verifier._validate_resource({"aud": []}) is False

    def test_no_aud_returns_false(self) -> None:
        verifier = self._make()
        assert verifier._validate_resource({}) is False

    def test_string_aud_match_returns_true(self) -> None:
        verifier = self._make()
        assert verifier._validate_resource({"aud": SERVER_URL}) is True

    def test_string_aud_mismatch_returns_false(self) -> None:
        verifier = self._make()
        assert verifier._validate_resource({"aud": "https://wrong.example.com"}) is False
