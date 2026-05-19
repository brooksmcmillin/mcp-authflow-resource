# API Reference

The full public surface of `mcp_authflow_resource`, generated from docstrings.

::: mcp_authflow_resource
    options:
      show_root_heading: false
      show_root_toc_entry: false
      members: false

## Modules

- [**Auth**](auth.md): `IntrospectionTokenVerifier`, `ClientAuthMethod`, `is_safe_url`
- [**OAuth Discovery**](oauth-discovery.md): `register_oauth_discovery_endpoints`
- [**Friction**](friction.md): `FrictionRegistry`, `ControllerConfig`, `ToolFrictionConfig`, decorators
- [**Middleware**](middleware.md): `NormalizePathMiddleware`, `create_logging_middleware`
- [**Validation**](validation.md): `validate_list_response`, `validate_dict_response`, `json_error`

Everything in the **Auth**, **OAuth Discovery**, and **Friction** modules is re-exported from the top level. `from mcp_authflow_resource import IntrospectionTokenVerifier` is the same as importing from the submodule.
