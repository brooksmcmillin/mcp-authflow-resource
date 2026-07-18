# Configuration

Configuration lives in code: every option is a constructor argument to [`IntrospectionTokenVerifier`][mcp_authflow_resource.IntrospectionTokenVerifier], [`register_oauth_discovery_endpoints`][mcp_authflow_resource.register_oauth_discovery_endpoints], or [`init_friction`][mcp_authflow_resource.init_friction], and the package reads nothing from environment variables.

## Token verification

```python
from mcp_authflow_resource import IntrospectionTokenVerifier

verifier = IntrospectionTokenVerifier(
    introspection_endpoint="https://auth.example.com/introspect",
    server_url="https://mcp.example.com",

    # RFC 8707 resource binding (default True; set False only for single-RS deployments)
    validate_resource=True,

    # Auth to the introspection endpoint itself (RFC 7662 §2.1)
    client_id=None,
    client_secret=None,
    client_auth_method="client_secret_basic",  # "client_secret_basic" | "client_secret_post" | "bearer" | "none"
)
```

| `client_auth_method` | Sent as | Requires |
|---|---|---|
| `"client_secret_basic"` (default when `client_secret` set) | `Authorization: Basic base64(client_id:client_secret)` | `client_id` + `client_secret` |
| `"client_secret_post"` | `client_id` and `client_secret` form fields in the POST body | `client_id` + `client_secret` |
| `"bearer"` | `Authorization: Bearer <client_secret>` | `client_secret` |
| `"none"` (default when `client_secret` unset) | no auth | (nothing) |

## OAuth discovery

```python
from mcp_authflow_resource import register_oauth_discovery_endpoints

register_oauth_discovery_endpoints(
    app,
    server_url="https://mcp.example.com",
    auth_server_public_url="https://auth.example.com",
    scopes=["read", "write"],
    resource_documentation="https://docs.example.com/mcp",
)
```

`server_url` is what gets advertised as `resource` and `aud`. `auth_server_public_url` is where clients are sent for authorization, and should be the *public* URL even if your resource server reaches the auth server over an internal address.

### CORS for browser clients

`cors_header_builder` is an optional `(Request) -> dict[str, str]` callable. When provided, the authorization-server metadata endpoints (`/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration`, and their path-scoped variants) add the returned headers to every response and answer `OPTIONS` preflight requests with an empty body. When it is `None` (the default), no CORS headers are added and the endpoints only handle `GET`.

Browser-based MCP clients send a CORS preflight before reading this metadata, so a builder is required for any browser-facing deployment:

```python
from starlette.requests import Request

def cors_headers(request: Request) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
    }

register_oauth_discovery_endpoints(
    app,
    server_url="https://mcp.example.com",
    auth_server_public_url="https://auth.example.com",
    cors_header_builder=cors_headers,
)
```

## Friction control

Friction is configured at startup via [`init_friction`][mcp_authflow_resource.init_friction]. The full parameter set, including per-tool and per-group targets, is covered in the [Friction Control guide](friction.md).

```python
from mcp_authflow_resource import (
    ControllerConfig,
    FrictionRegistry,
    ToolFrictionConfig,
    init_friction,
)

init_friction(FrictionRegistry(
    default_config=ControllerConfig(window_size=100, warmup_calls=20),
    tool_configs={
        "delete_task": ToolFrictionConfig(target_rate=0.03),
    },
))
```

## Logging

Configure Python's standard `logging` module. No extra config knobs. The logger names this package uses:

| Logger | Level | What's there |
|---|---|---|
| `mcp_authflow_resource.friction` | INFO | Friction check/record events. |
| `mcp_authflow_resource.friction.block` | WARNING | Blocked calls. |
| `mcp_authflow_resource.friction.registry` | DEBUG | Per-client lifecycle. |

Pipe these into your structured log stack for observability dashboards.
