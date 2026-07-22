# mcp-authflow-resource

OAuth 2.0 Resource Server framework for [MCP](https://modelcontextprotocol.io/) servers. Validate tokens and control tool-call rates with a proportional feedback loop.

Pair with [mcp-authflow](https://github.com/brooksmcmillin/mcp-authflow) on the authorization server side.

## Features

- **Token verification** via RFC 7662 introspection with SSRF protection
- **OAuth discovery** endpoints (RFC 9728, RFC 8414, OIDC)
- **Friction control** -- dynamic tool-call rate limiting using a proportional feedback loop
- **Response validation** helpers for MCP tool implementations
- **ASGI middleware** for path normalization and request logging
- **Async-first** design, built on Starlette and MCP SDK

## Installation

```bash
pip install mcp-authflow-resource
```

## Quick Start: Protect an MCP Server in 5 Minutes

```python
from mcp.server.fastmcp.server import FastMCP
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from mcp_authflow_resource import (
    IntrospectionTokenVerifier,
    register_oauth_discovery_endpoints,
)

# 1. Create a token verifier pointing at your auth server
verifier = IntrospectionTokenVerifier(
    introspection_endpoint="http://localhost:8000/introspect",
    server_url="https://mcp.example.com",
)

# 2. Create an MCP server with OAuth protection
app = FastMCP(
    name="My Protected MCP Server",
    token_verifier=verifier,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl("https://auth.example.com"),
        required_scopes=["read"],
        resource_server_url=AnyHttpUrl("https://mcp.example.com"),
    ),
)

# 3. Register OAuth discovery endpoints (RFC 9728 + RFC 8414)
register_oauth_discovery_endpoints(
    app,
    server_url="https://mcp.example.com",
    auth_server_public_url="https://auth.example.com",
    scopes=["read"],
)


# 4. Define tools -- they're now protected by OAuth
@app.tool()
async def hello(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"
```

That's it. Clients must now present a valid Bearer token to call any tool.

## Architecture

```
  MCP Client
      |
      | Bearer token
      v
+---------------------------+
|    Resource Server         |   <-- this package
|    (your MCP tools)        |
|                           |
|  1. Extract Bearer token  |
|  2. Introspect token  ----+---> Auth Server (/introspect)
|  3. Check scopes          |         |
|  4. Friction check        |    "active": true/false
|  5. Execute tool          |
+---------------------------+
```

### Token Verification Flow

1. Client sends request with `Authorization: Bearer <token>` header
2. `IntrospectionTokenVerifier` calls the auth server's introspection endpoint (RFC 7662)
3. Auth server responds with token metadata (`active`, `scope`, `client_id`, `exp`, `aud`)
4. If active and scopes match, the tool executes
5. If `validate_resource=True`, the `aud` claim must match the server URL (RFC 8707)

## API Reference

### Token Verification

```python
from mcp_authflow_resource import IntrospectionTokenVerifier

verifier = IntrospectionTokenVerifier(
    introspection_endpoint="http://auth-server:8000/introspect",
    server_url="https://mcp.example.com",
    validate_resource=True,  # Default; RFC 8707 resource binding. Set False only for single-RS deployments
)

# Returns AccessToken or None
token = await verifier.verify_token("Bearer_token_here")
# token.client_id, token.scopes, token.expires_at, token.resource
```

#### Authenticating to a Protected `/introspect` Endpoint

RFC 7662 §2.1 requires the introspection endpoint to authenticate its callers. Pass credentials via the optional `client_id`, `client_secret`, and `client_auth_method` keyword arguments — the verifier will include them on every introspection POST.

```python
# Most common: HTTP Basic (RFC 6749 §2.3.1) — the default when credentials are given.
verifier = IntrospectionTokenVerifier(
    introspection_endpoint="https://auth.example.com/introspect",
    server_url="https://mcp.example.com",
    client_id="my-resource-server",
    client_secret="...",
    # client_auth_method="client_secret_basic"  (default)
)

# Form parameters in the POST body (RFC 6749 §2.3.1, alt form).
verifier = IntrospectionTokenVerifier(
    introspection_endpoint="https://auth.example.com/introspect",
    server_url="https://mcp.example.com",
    client_id="my-resource-server",
    client_secret="...",
    client_auth_method="client_secret_post",
)

# Single shared bearer secret (RFC 6750-style; used by mcp-authflow).
verifier = IntrospectionTokenVerifier(
    introspection_endpoint="https://auth.example.com/introspect",
    server_url="https://mcp.example.com",
    client_secret="shared-secret",
    client_auth_method="bearer",
)
```

| `client_auth_method` | Sent as | Requires |
|---|---|---|
| `"client_secret_basic"` (default when `client_secret` set) | `Authorization: Basic base64(client_id:client_secret)` | `client_id` + `client_secret` |
| `"client_secret_post"` | `client_id` and `client_secret` form fields in the POST body | `client_id` + `client_secret` |
| `"bearer"` | `Authorization: Bearer <client_secret>` | `client_secret` |
| `"none"` (default when `client_secret` unset) | no auth | — |

### SSRF Protection

```python
from mcp_authflow_resource import is_safe_url

is_safe_url("https://api.example.com")           # True (HTTPS always safe)
is_safe_url("http://localhost:8000")              # True (localhost allowed by default)
is_safe_url("http://mcp-auth")                    # True (Docker/k8s service name)
is_safe_url("http://evil.example.com")            # False (HTTP to external host)
is_safe_url("http://localhost", allow_localhost=False)  # False
```

### OAuth Discovery

Auto-configures `.well-known` endpoints so MCP clients can discover your auth server:

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

**Registered endpoints:**

| Endpoint | Spec |
|----------|------|
| `GET /.well-known/oauth-protected-resource` | RFC 9728 |
| `GET /mcp/.well-known/oauth-protected-resource` | RFC 9728 (path-scoped) |
| `GET /.well-known/oauth-authorization-server` | RFC 8414 |
| `GET /.well-known/oauth-authorization-server/mcp` | RFC 8414 (path-scoped) |
| `GET /.well-known/openid-configuration` | OIDC Discovery |

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `server_url` | Public URL of this resource server (advertised as `resource`). |
| `auth_server_public_url` | Public URL of the authorization server clients are sent to. |
| `scopes` | Supported OAuth scopes. Defaults to `["read"]`. |
| `resource_documentation` | Optional URL included in protected resource metadata. |
| `cors_header_builder` | Optional `(Request) -> dict[str, str]` callable. When set, the authorization-server metadata endpoints add its headers to every response and answer `OPTIONS` preflight requests — required for browser-based MCP clients. When `None`, no CORS headers are added. |

**Browser-facing deployments (CORS):**

Browser MCP clients send a CORS preflight before fetching auth-server metadata. Pass a `cors_header_builder` so the metadata endpoints respond to `OPTIONS` and include the appropriate headers:

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

### Friction Control

Dynamic tool-call rate limiting that adjusts friction per-tool based on observed usage, converging toward configured targets. Inspired by proof-of-work difficulty adjustment.

#### Setup

```python
from mcp_authflow_resource import (
    ControllerConfig,
    FrictionRegistry,
    ToolFrictionConfig,
    ToolGroupConfig,
    friction_controlled,
    init_friction,
    record_tool_call,
)

# Initialize at server startup
init_friction(FrictionRegistry(
    default_config=ControllerConfig(
        window_size=100,        # Sliding window of last 100 calls
        time_decay_rate=0.001,  # ~11.5 min half-life for idle decay
        warmup_calls=20,        # No adjustment during first 20 calls
    ),
    tool_configs={
        "delete_task": ToolFrictionConfig(target_rate=0.03),  # 3% of calls
        "update_task": ToolFrictionConfig(target_rate=0.10),  # 10% of calls
    },
    tool_groups={
        "mutations": ToolGroupConfig(
            tools=["delete_task", "update_task"],
            aggregate_target=0.20,  # Combined 20% of all calls
        ),
    },
))
```

#### Decorators

```python
# Mutation tools: checks friction before execution, blocks if too high
@app.tool()
@friction_controlled()
async def delete_task(task_id: str) -> str:
    ...

# Read tools: records call without friction checks (for rate denominator)
@app.tool()
@record_tool_call()
async def get_tasks(status: str) -> str:
    ...
```

#### How It Works

The friction controller tracks tool calls in a sliding window and computes an exponential moving average (EMA) of each tool's usage rate. When a tool's rate exceeds its target, friction increases -- raising the cost and eventually blocking calls. When usage drops, friction decreases (2x faster than it rises).

```
Friction Level    Effect
---------------------------------------------------------------------------
0.0 - 0.59        NONE/LOW/MEDIUM -- tool executes normally
0.60 - 0.94       HIGH -- justification_required=True in FrictionResult
0.95 - 1.0        BLOCKED -- tool call denied, error returned
```

**Key parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window_size` | 100 | Number of recent calls to track |
| `time_decay_rate` | 0.001 | Exponential friction decay (~11.5 min half-life) |
| `warmup_calls` | 20 | Calls before friction adjustment begins |
| `target_rate` | 0.05 | Desired tool usage fraction (0.0-1.0) |
| `justification_threshold` | 0.6 | Friction level requiring justification |
| `hard_block_threshold` | 0.95 | Friction level that blocks the call |
| `saturation_threshold` | 0.9 | Triggers automatic relief if sustained |

**Security-relevant defaults:** two parameters ship disabled and must be set to
turn on the protection they gate.

| Parameter | Default | Safety note |
|-----------|---------|-------------|
| `default_budget` | `inf` | Cost enforcement is disabled; tool-use spending is unbounded until you set a finite per-client budget |
| `saturation_window` | `0` | Automatic saturation relief is disabled, so sustained saturation can stay near block until normal decay brings it down |

The other `ControllerConfig` fields (`ema_alpha`, `adjustment_rate`,
`asymmetric_decay`, `dead_zone`, `saturation_relief_rate`) are adjustment-loop
tuning knobs; see the [friction control guide](docs/friction.md) and the
`ControllerConfig` API reference for details.

#### Observability

Friction events are emitted as structured JSON via Python's `logging` module:

```python
# Logger names
"mcp_authflow_resource.friction"         # check/record events (INFO)
"mcp_authflow_resource.friction.block"   # blocked calls (WARNING)
"mcp_authflow_resource.friction.registry"  # client lifecycle (DEBUG)
```

Event types: `friction_check`, `friction_block`, `friction_justification`, `friction_saturation`

Fields: `event_type`, `client_id`, `tool_name`, `friction_level`, `ema_rate`, `target_rate`, `cost`, `allowed`

### Response Validation

Helpers for validating API responses in MCP tool implementations:

```python
from mcp_authflow_resource.validation import (
    json_error,
    validate_list_response,
    validate_dict_response,
)

# Returns (list, None) on success or ([], "error message") on failure
items, error = validate_list_response(api_response, context="tasks")
if error:
    return json_error(error)
```

### Middleware

```python
from mcp_authflow_resource.middleware import NormalizePathMiddleware, create_logging_middleware

# Normalize trailing slashes: /mcp/ -> /mcp
app.add_middleware(NormalizePathMiddleware)

# Debug logging with auth header masking
app = create_logging_middleware(app, mask_auth=True)
```

## Full Example: Auth Server + Resource Server

A complete working example using both packages together:

**auth_server.py** (authorization server):

```python
import secrets
import time
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

def invalid_request(message: str) -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_request", "error_description": message},
        status_code=400,
    )


def parse_scope_field(value: object) -> str:
    return " ".join(str(value or "").split())


class MemoryTokenStorage:
    def __init__(self) -> None:
        self._tokens: dict[str, dict[str, object]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        self._tokens.clear()

    async def store_token(
        self,
        *,
        token: str,
        client_id: str,
        scopes: list[str],
        expires_at: int,
        resource: str,
    ) -> None:
        self._tokens[token] = {
            "client_id": client_id,
            "scopes": scopes,
            "expires_at": expires_at,
            "resource": resource,
        }

    async def load_token(self, token: str) -> dict[str, object] | None:
        return self._tokens.get(token)


storage = MemoryTokenStorage()

async def token(request: Request) -> JSONResponse:
    form = await request.form()
    client_id = str(form.get("client_id", ""))
    if not client_id:
        return invalid_request("client_id is required")

    access_token = secrets.token_urlsafe(32)
    scopes = parse_scope_field(form.get("scope"))

    # RFC 8707 resource indicator. Bind the token to the resource server it is
    # for so introspection can return a matching `aud` claim; default to this
    # example's resource server when the client doesn't request one.
    resource = str(form.get("resource") or "http://localhost:8001")

    await storage.store_token(
        token=access_token,
        client_id=client_id,
        scopes=scopes.split(),
        expires_at=int(time.time()) + 3600,
        resource=resource,
    )

    return JSONResponse({
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "scope": scopes,
    })

async def introspect(request: Request) -> JSONResponse:
    form = await request.form()
    token_str = str(form.get("token", ""))
    data = await storage.load_token(token_str)

    if not data or data["expires_at"] < time.time():
        return JSONResponse({"active": False})

    return JSONResponse({
        "active": True,
        "client_id": data["client_id"],
        "scope": " ".join(data["scopes"]),
        "exp": data["expires_at"],
        # `aud` binds the token to the resource server. Without it the
        # verifier's default validate_resource=True rejects every token.
        "aud": data["resource"],
    })

@asynccontextmanager
async def lifespan(app):
    await storage.initialize()
    yield
    await storage.close()

app = Starlette(
    routes=[
        Route("/token", token, methods=["POST"]),
        Route("/introspect", introspect, methods=["POST"]),
    ],
    lifespan=lifespan,
)
```

**resource_server.py** (MCP resource server):

```python
from mcp.server.fastmcp.server import FastMCP

from mcp_authflow_resource import (
    IntrospectionTokenVerifier,
    register_oauth_discovery_endpoints,
)

verifier = IntrospectionTokenVerifier(
    introspection_endpoint="http://localhost:8000/introspect",
    server_url="http://localhost:8001",
)

app = FastMCP(name="Example MCP", token_verifier=verifier)

register_oauth_discovery_endpoints(
    app,
    server_url="http://localhost:8001",
    auth_server_public_url="http://localhost:8000",
)

@app.tool()
async def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"
```

**Run both:**

```bash
# Terminal 1: Auth server
uvicorn auth_server:app --port 8000

# Terminal 2: Resource server
python resource_server.py  # MCP SDK handles transport
```

**Test the flow:**

```bash
# Get a token bound to the resource server (RFC 8707 resource indicator)
TOKEN=$(curl -s -X POST http://localhost:8000/token \
  -d "client_id=test&scope=read&resource=http://localhost:8001" \
  | jq -r .access_token)

# Confirm introspection returns the matching `aud`, so the verifier accepts it
curl -s -X POST http://localhost:8000/introspect -d "token=$TOKEN" | jq .

# The resource server advertises its auth server here (unauthenticated)
curl http://localhost:8001/.well-known/oauth-protected-resource

# Call an MCP tool over the MCP protocol with the token in the Authorization
# header, e.g. Authorization: Bearer $TOKEN
```

## License

MIT
