# Changelog

## 0.4.0

### Added

- **`IntrospectionTokenVerifier` caller authentication (RFC 7662 §2.1).**
  The verifier now accepts optional `client_id`, `client_secret`, and
  `client_auth_method` keyword arguments and will authenticate itself when
  calling `/introspect`. Supported methods:

  - `"client_secret_basic"` (default when credentials are supplied) — RFC
    6749 §2.3.1 HTTP Basic: `Authorization: Basic base64(id:secret)`.
  - `"client_secret_post"` — RFC 6749 §2.3.1 form parameters in the POST
    body.
  - `"bearer"` — RFC 6750 bearer auth with a single shared secret
    (`Authorization: Bearer <client_secret>`), for authorization servers
    that protect `/introspect` with a shared secret rather than per-client
    credentials.
  - `"none"` — explicit no-auth (also the behavior when `client_secret` is
    omitted).

  ```python
  from mcp_authflow_resource import IntrospectionTokenVerifier

  verifier = IntrospectionTokenVerifier(
      introspection_endpoint="https://auth.example.com/introspect",
      server_url="https://mcp.example.com",
      client_id="my-resource-server",
      client_secret="...",
      # client_auth_method defaults to "client_secret_basic"
  )
  ```

  The `ClientAuthMethod` `Literal` is exported from the package root for
  callers who want a typed parameter.

### Compatibility

- No breaking changes. Existing
  `IntrospectionTokenVerifier(introspection_endpoint=..., server_url=...)`
  callers send the request without authentication, exactly as before.

## 0.3.0

### Security

- **SSRF (`is_safe_url`):** Replaced naïve `url.startswith()` checks with a
  proper `urlparse` + `ipaddress`-based parser. The previous implementation
  was bypassable in several ways that the new one rejects:

  - IPv6 literals other than `::1` (e.g. `http://[2001:db8::1]/`)
  - IPv4-mapped IPv6 forms (`::ffff:127.0.0.1`) now follow the same loopback
    rule as bare `127.0.0.1`
  - Userinfo injection (`http://evil.com@mcp-auth/` parses to host `mcp-auth`,
    which is the only safe interpretation)
  - Decimal IP forms (`http://2130706433/` for `127.0.0.1`)
  - Hex-dotted IP forms (`http://0x7f.0x0.0x0.0x1/`)
  - Null-host URLs (`http:///path`)
  - Percent-encoded hostnames (`http://%6C%6F%63%61%6C%68%6F%73%74/`)
  - Single-segment HTTP hostnames are now required to match an RFC 1123 DNS
    label (`[a-z][a-z0-9-]*`), preventing numeric or trailing-hyphen forms
    from being accepted as Docker service names.

  The accepted set is unchanged for the documented allowlist — HTTPS, loopback,
  Docker single-segment hostnames, and `*.cluster.local` — but bypasses
  outside that allowlist are now closed.

- **`create_logging_middleware`:** Now raises `RuntimeError` unless the
  environment variable `MCP_ENABLE_VERBOSE_LOGGING=1` is explicitly set
  (CWE-532). The middleware logs full request bodies (up to 1000 bytes),
  all headers, and 400-response bodies — when used with MCP servers that
  includes tool arguments and other personal data forwarded to log sinks
  like Loki. The opt-in env var prevents accidental production activation.

  Existing test code that calls `create_logging_middleware` directly will
  need to set `MCP_ENABLE_VERBOSE_LOGGING=1` (e.g. via
  `monkeypatch.setenv` in pytest); see `tests/test_middleware.py` for the
  pattern.

## 0.2.0

### Breaking changes

- Renamed Python import from `mcp_resource_framework` to
  `mcp_authflow_resource` so it matches the PyPI distribution name. The
  package is now installed and imported under the same name:

  ```python
  # Before
  from mcp_resource_framework import IntrospectionTokenVerifier

  # After
  from mcp_authflow_resource import IntrospectionTokenVerifier
  ```

  No compatibility shim is provided; update imports directly.
- The GitHub repository moved from `brooksmcmillin/mcpauth-resource` to
  `brooksmcmillin/mcp-authflow-resource`. GitHub redirects the old URLs,
  but bookmarks and CI configurations should be updated.
- The friction logger names changed from
  `mcp_resource_framework.friction*` to
  `mcp_authflow_resource.friction*`. Update any logging filters that
  pin the old namespace.

## 0.1.0

Initial release on PyPI as `mcp-authflow-resource` (imported as
`mcp_resource_framework`). OAuth 2.0 Resource Server primitives for
MCP: introspection-based token verification with SSRF protection,
RFC 9908 / RFC 8414 / OIDC discovery endpoints, normalize-path and
logging middleware, and the proportional-feedback friction
controller for per-tool rate management.
