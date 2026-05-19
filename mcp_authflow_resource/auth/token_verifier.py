"""Token verifier implementation using OAuth 2.0 Token Introspection (RFC 7662)."""

import base64
import logging
from typing import Any, Literal

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url

from mcp_authflow_resource.auth.ssrf_protection import is_safe_url

logger = logging.getLogger(__name__)

ClientAuthMethod = Literal[
    "none",
    "client_secret_basic",
    "client_secret_post",
    "bearer",
]


class IntrospectionTokenVerifier(TokenVerifier):
    """Token verifier that uses OAuth 2.0 Token Introspection (RFC 7662).

    Optional caller authentication on ``/introspect`` (RFC 7662 §2.1) is
    supported via the ``client_id`` / ``client_secret`` / ``client_auth_method``
    parameters. When ``client_secret`` is unset the request is sent without
    any authentication (backwards-compatible default).

    Supported ``client_auth_method`` values:

    - ``"client_secret_basic"`` (default when credentials are given) — RFC 6749
      §2.3.1 HTTP Basic auth: ``Authorization: Basic base64(client_id:client_secret)``.
      Requires both ``client_id`` and ``client_secret``.
    - ``"client_secret_post"`` — RFC 6749 §2.3.1 form parameters: adds
      ``client_id`` and ``client_secret`` to the POST body. Requires both.
    - ``"bearer"`` — RFC 6750 bearer auth, used by deployments that protect
      the introspection endpoint with a single shared secret:
      ``Authorization: Bearer <client_secret>``. Requires only ``client_secret``.
    - ``"none"`` — explicit no-auth (same as omitting ``client_secret``).
    """

    def __init__(
        self,
        introspection_endpoint: str,
        server_url: str,
        validate_resource: bool = False,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        client_auth_method: ClientAuthMethod = "client_secret_basic",
    ):
        self.introspection_endpoint = introspection_endpoint
        self.server_url = server_url
        self.validate_resource = validate_resource
        self.resource_url = resource_url_from_server_url(server_url)
        self._client_id = client_id
        self._client_secret = client_secret
        self._client_auth_method: ClientAuthMethod = (
            "none" if client_secret is None else client_auth_method
        )

        if self._client_auth_method in ("client_secret_basic", "client_secret_post"):
            if self._client_id is None or self._client_secret is None:
                raise ValueError(
                    f"client_auth_method={self._client_auth_method!r} requires "
                    "both client_id and client_secret"
                )
        elif self._client_auth_method == "bearer" and self._client_secret is None:
            raise ValueError("client_auth_method='bearer' requires client_secret")

    def _apply_client_auth(
        self,
        headers: dict[str, str],
        data: dict[str, str],
    ) -> None:
        """Apply the configured client auth to ``headers`` and ``data`` in place."""
        if self._client_auth_method == "client_secret_basic":
            assert self._client_id is not None
            assert self._client_secret is not None
            creds = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        elif self._client_auth_method == "client_secret_post":
            assert self._client_id is not None
            assert self._client_secret is not None
            data["client_id"] = self._client_id
            data["client_secret"] = self._client_secret
        elif self._client_auth_method == "bearer":
            assert self._client_secret is not None
            headers["Authorization"] = f"Bearer {self._client_secret}"

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify token via introspection endpoint."""
        import httpx  # noqa: PLC0415

        # Validate URL to prevent SSRF attacks
        if not is_safe_url(self.introspection_endpoint, allow_localhost=True):
            logger.warning(
                "Rejecting introspection endpoint with unsafe scheme: %s",
                self.introspection_endpoint,
            )
            return None

        # Configure secure HTTP client
        timeout = httpx.Timeout(10.0, connect=5.0)
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

        # Only verify SSL for HTTPS URLs
        verify_ssl = self.introspection_endpoint.startswith("https://")

        headers: dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
        data: dict[str, str] = {"token": token}
        self._apply_client_auth(headers, data)

        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            verify=verify_ssl,  # Enforce SSL verification for HTTPS only
        ) as client:
            try:
                response = await client.post(
                    self.introspection_endpoint,
                    data=data,
                    headers=headers,
                )

                if response.status_code != 200:
                    logger.debug("Token introspection returned status %s", response.status_code)
                    return None

                data_resp = response.json()
                if not data_resp.get("active", False):
                    logger.debug("Token marked as inactive")
                    return None

                # RFC 8707 resource validation (only when --oauth-strict is set)
                if self.validate_resource and not self._validate_resource(data_resp):
                    logger.warning(
                        "Token resource validation failed. Expected: %s", self.resource_url
                    )
                    return None

                access_token = AccessToken(
                    token=token,
                    client_id=data_resp.get("client_id", "unknown"),
                    scopes=data_resp.get("scope", "").split() if data_resp.get("scope") else [],
                    expires_at=data_resp.get("exp"),
                    resource=data_resp.get("aud"),  # Include resource in token
                )
                return access_token
            except Exception as e:
                logger.warning("Token introspection failed: %s", e)
                return None

    def _validate_resource(self, token_data: dict[str, Any]) -> bool:
        """Validate token was issued for this resource server."""
        if not self.server_url or not self.resource_url:
            return False  # Fail if strict validation requested but URLs missing

        # Check 'aud' claim first (standard JWT audience)
        aud = token_data.get("aud")
        if isinstance(aud, list):
            return any(self._is_valid_resource(audience) for audience in aud)
        elif aud:
            return self._is_valid_resource(aud)

        # No resource binding - invalid per RFC 8707
        return False

    def _is_valid_resource(self, resource: str) -> bool:
        """Check if resource matches this server using hierarchical matching."""
        if not self.resource_url:
            return False

        return check_resource_allowed(
            requested_resource=self.resource_url, configured_resource=resource
        )
