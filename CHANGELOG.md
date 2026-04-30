# Changelog

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
RFC 9728 / RFC 8414 / OIDC discovery endpoints, normalize-path and
logging middleware, and the proportional-feedback friction
controller for per-tool rate management.
