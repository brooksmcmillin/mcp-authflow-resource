# Quickstart

Wire OAuth onto a `FastMCP` server. After this, every tool requires a valid Bearer token.

## Install

```bash
pip install mcp-authflow-resource
```

## A protected MCP server

```python title="resource_server.py"
from mcp.server.fastmcp.server import FastMCP
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from mcp_authflow_resource import (
    IntrospectionTokenVerifier,
    register_oauth_discovery_endpoints,
)

# 1. Point a verifier at your authorization server's introspection endpoint
verifier = IntrospectionTokenVerifier(
    introspection_endpoint="http://localhost:8000/introspect",
    server_url="https://mcp.example.com",
)

# 2. Create an MCP server that uses it
app = FastMCP(
    name="My Protected MCP Server",
    token_verifier=verifier,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl("https://auth.example.com"),
        required_scopes=["read"],
        resource_server_url=AnyHttpUrl("https://mcp.example.com"),
    ),
)

# 3. Advertise discovery endpoints so MCP clients can find your auth server
register_oauth_discovery_endpoints(
    app,
    server_url="https://mcp.example.com",
    auth_server_public_url="https://auth.example.com",
    scopes=["read"],
)


# 4. Define tools (they're now protected by OAuth)
@app.tool()
async def hello(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"
```

Clients now need a valid Bearer token to call `hello`.

## Authenticate to a protected `/introspect`

If your auth server protects its own introspection endpoint (which [RFC 7662 §2.1](https://datatracker.ietf.org/doc/html/rfc7662#section-2.1) requires), pass credentials when constructing the verifier:

=== "HTTP Basic (default)"

    ```python
    verifier = IntrospectionTokenVerifier(
        introspection_endpoint="https://auth.example.com/introspect",
        server_url="https://mcp.example.com",
        client_id="my-resource-server",
        client_secret="...",
        # client_auth_method="client_secret_basic"  (default)
    )
    ```

=== "Form parameters"

    ```python
    verifier = IntrospectionTokenVerifier(
        introspection_endpoint="https://auth.example.com/introspect",
        server_url="https://mcp.example.com",
        client_id="my-resource-server",
        client_secret="...",
        client_auth_method="client_secret_post",
    )
    ```

=== "Bearer secret"

    ```python
    verifier = IntrospectionTokenVerifier(
        introspection_endpoint="https://auth.example.com/introspect",
        server_url="https://mcp.example.com",
        client_secret="shared-secret",
        client_auth_method="bearer",
    )
    ```

See the [`IntrospectionTokenVerifier`][mcp_authflow_resource.auth.token_verifier.IntrospectionTokenVerifier] reference for the complete keyword-argument list, including `introspection_cache_ttl` (introspection caching) and `validate_resource` (RFC 8707 audience binding).

## Add friction control

Once tokens are working, you can add dynamic rate limiting per tool. The [Friction Control guide](friction.md) walks through the registry, decorators, and tuning parameters in detail.

```python
from mcp_authflow_resource import (
    ControllerConfig,
    FrictionRegistry,
    ToolFrictionConfig,
    friction_controlled,
    init_friction,
)

init_friction(FrictionRegistry(
    default_config=ControllerConfig(window_size=100, warmup_calls=20),
    tool_configs={
        "delete_task": ToolFrictionConfig(target_rate=0.03),
    },
))


@app.tool()
@friction_controlled()
async def delete_task(task_id: str) -> str:
    ...
```

## Next steps

- [Architecture](architecture.md): the token-verification flow.
- [Friction Control](friction.md): the rate-limiting subsystem.
- [API Reference](api/index.md): module-by-module reference.
- [mcp-authflow](https://github.com/brooksmcmillin/mcp-authflow): the matching authorization server.
