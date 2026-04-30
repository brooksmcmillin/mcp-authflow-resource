"""SSRF (Server-Side Request Forgery) protection utilities."""


def is_safe_url(url: str, allow_localhost: bool = True) -> bool:
    """Check if a URL is safe to request (SSRF protection).

    Args:
        url: The URL to validate
        allow_localhost: Whether to allow localhost/127.0.0.1 URLs

    Returns:
        True if the URL is considered safe, False otherwise

    Safe URLs must:
    - Use HTTPS for production endpoints
    - Use HTTP only for localhost (if allow_localhost is True)
    - Use HTTP for Docker internal hostnames (e.g., http://mcp-auth:)
    """
    # Allow HTTPS
    if url.startswith("https://"):
        return True

    # Allow localhost and Docker internal hostnames if enabled
    if allow_localhost and url.startswith(("http://localhost", "http://127.0.0.1")):
        return True

    # Allow Docker/k8s internal hostnames:
    # - Single-segment hostnames with no dots (e.g., http://mcp-auth, http://backend)
    # - Kubernetes FQDN (e.g., http://mcp-auth.taskmanager.svc.cluster.local)
    if url.startswith("http://"):
        # Extract hostname (between http:// and the next : or /)
        rest = url[len("http://") :]
        host_part = rest.split(":")[0].split("/")[0]
        # Exclude localhost/loopback when allow_localhost is False
        if not allow_localhost and host_part in ("localhost", "127.0.0.1"):
            return False
        if "." not in host_part or host_part.endswith(".cluster.local"):
            return True

    return False
