"""MCP Resource Framework - OAuth 2.0 Resource Server components for MCP.

Provides building blocks for MCP servers that validate OAuth tokens and
control tool-call rates:

- **Token verification** — RFC 7662 token introspection against any
  mcpauth-compatible issuer.
- **OAuth discovery** — Auto-configures ``.well-known`` endpoints
  (RFC 8414 / RFC 9908).
- **Friction control** — Dynamic tool-call rate limiting via proportional
  feedback loop.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-authflow-resource")
except PackageNotFoundError:  # pragma: no cover - package not installed (e.g. source tree)
    __version__ = "0.0.0+unknown"

from mcp_authflow_resource.auth import (
    ClientAuthMethod,
    IntrospectionTokenVerifier,
    is_safe_url,
)
from mcp_authflow_resource.friction import (
    ControllerConfig,
    FrictionRegistry,
    ToolFrictionConfig,
    ToolGroupConfig,
    friction_controlled,
    init_friction,
    record_tool_call,
)
from mcp_authflow_resource.oauth_discovery import register_oauth_discovery_endpoints

__all__ = [
    # Version
    "__version__",
    # Auth
    "ClientAuthMethod",
    "IntrospectionTokenVerifier",
    "is_safe_url",
    # Friction
    "ControllerConfig",
    "FrictionRegistry",
    "ToolFrictionConfig",
    "ToolGroupConfig",
    "friction_controlled",
    "init_friction",
    "record_tool_call",
    # OAuth discovery
    "register_oauth_discovery_endpoints",
]
