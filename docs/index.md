# mcp-authflow-resource

OAuth 2.0 **Resource Server** framework for [MCP](https://modelcontextprotocol.io/) servers: validate tokens and control tool-call rates with a proportional feedback loop, paired with [**mcp-authflow**](https://github.com/brooksmcmillin/mcp-authflow) on the authorization server side.

---

## What's in the box

- **Token verification** via RFC 7662 introspection with SSRF protection
- **OAuth discovery** endpoints (RFC 9728, RFC 8414, OIDC)
- **Friction control**: dynamic tool-call rate limiting via proportional feedback loop
- **Response validation** helpers for MCP tool implementations
- **ASGI middleware** for path normalization and request logging
- **Async-first** design, built on Starlette and the MCP SDK

## Install

```bash
pip install mcp-authflow-resource
```

## Where to go next

<div class="grid cards" markdown>

- :material-rocket-launch: **[Quickstart](quickstart.md)**

    Protect an MCP server with OAuth in about 30 lines.

- :material-sitemap: **[Architecture](architecture.md)**

    The token verification flow, end to end.

- :material-speedometer: **[Friction Control](friction.md)**

    Dynamic rate limiting that adapts to actual tool usage.

- :material-api: **[API Reference](api/index.md)**

    Module-by-module reference, generated from docstrings.

</div>

Start with the Quickstart to wire OAuth onto a `FastMCP` server; the Friction Control guide covers the adaptive rate-limiting subsystem in depth.

## How it fits with mcp-authflow

This package validates tokens; it does not issue them. By default it expects an [RFC 7662](https://datatracker.ietf.org/doc/html/rfc7662) introspection endpoint that returns `active`, `client_id`, `scope`, `exp`, and optionally `aud`. [`mcp-authflow`][mcp-authflow] is the matching authorization server, but any RFC 7662 issuer (Keycloak, Auth0, Hydra, custom) will work.

[mcp-authflow]: https://github.com/brooksmcmillin/mcp-authflow

## License

[MIT](https://github.com/brooksmcmillin/mcp-authflow-resource/blob/main/LICENSE)
