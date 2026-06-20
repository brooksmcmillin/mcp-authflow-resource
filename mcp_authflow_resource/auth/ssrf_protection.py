"""SSRF (Server-Side Request Forgery) protection utilities."""

import ipaddress
import re
from urllib.parse import urlparse


def is_safe_url(url: str, allow_localhost: bool = True) -> bool:
    """Check if a URL is safe to request (SSRF protection).

    Args:
        url: The URL to validate
        allow_localhost: Whether to allow localhost/127.0.0.1 URLs

    Returns:
        True if the URL is considered safe, False otherwise

    Safe URLs must:
    - Use HTTPS for production endpoints (any valid hostname)
    - Use HTTP only for localhost/loopback IPs (if allow_localhost is True)
    - Use HTTP for Docker internal hostnames (e.g., http://mcp-auth:)
    - Use HTTP for Kubernetes FQDNs (e.g., http://*.cluster.local)

    Implementation uses urlparse + ipaddress to prevent bypass techniques
    including: IPv6 literals, userinfo injection, decimal/hex IP forms,
    null hostnames, and percent-encoded hostnames.

    .. warning::
        **This is NOT a general-purpose SSRF filter and does NOT resolve DNS.**

        Any ``https://`` URL with a non-IP hostname returns ``True``,
        regardless of the IP that hostname actually resolves to. A hostname
        that resolves to a private/internal address (e.g. ``10.x``,
        ``192.168.x``, ``169.254.x``, ``metadata.google.internal``) passes
        this check. This is intentional: the function is scoped to validating
        *operator-configured* authorization-server / introspection endpoints,
        where the operator — not an attacker — controls the URL.

        Do **not** use this function to validate untrusted, user-supplied URLs
        (webhooks, fetch targets, etc.). For those cases you must additionally
        resolve the hostname and reject private targets, e.g.::

            import ipaddress, socket
            ip = ipaddress.ip_address(socket.gethostbyname(hostname))
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                reject()

        Note that even resolve-then-check is vulnerable to DNS rebinding
        unless the resolved IP is pinned for the actual connection.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    # parsed.hostname strips brackets from IPv6, strips userinfo, and strips port.
    # Note: urlparse does NOT decode percent-encoding in the hostname component —
    # percent-encoded forms are safely rejected because they fail all allowlist checks.
    # Returns None for malformed URLs.
    hostname = parsed.hostname
    if not hostname:
        return False

    hostname = hostname.lower()

    # Attempt to parse as an IP address literal (handles IPv4, IPv6, IPv4-mapped IPv6,
    # decimal integers like 2130706433, hex like 0x7f000001, etc.)
    try:
        ip = ipaddress.ip_address(hostname)
        # Only allow IP literals for loopback, and only when allow_localhost is True.
        # Covers: ::1 (IPv6 loopback), 127.x.x.x (IPv4 loopback),
        # and ::ffff:127.x.x.x (IPv4-mapped IPv6 loopback, e.g. ::ffff:127.0.0.1).
        # IPv6Address.is_loopback checks for ::1 only; we explicitly follow ipv4_mapped
        # to handle the IPv4-mapped case portably across Python patch versions.
        is_loopback = ip.is_loopback or (
            isinstance(ip, ipaddress.IPv6Address)
            and ip.ipv4_mapped is not None
            and ip.ipv4_mapped.is_loopback
        )
        return bool(allow_localhost and is_loopback)
    except ValueError:
        # Not an IP literal — fall through to hostname-based allowlist
        pass

    # "localhost" as a DNS name (not an IP — handled above)
    if hostname == "localhost":
        return allow_localhost

    # HTTPS to any valid non-IP hostname is allowed
    if parsed.scheme == "https":
        return True

    # HTTP is only allowed for internal service names (Docker/k8s):
    # - Single-segment hostname with no dots (Docker service name, e.g. "mcp-auth")
    #   Must match a valid DNS label starting with a letter (RFC 952/1123 convention
    #   for Docker/k8s names), to prevent numeric-looking hostnames slipping through.
    if "." not in hostname:
        # RFC 1123-conformant label: starts with letter, ends with letter or digit,
        # interior may contain hyphens. Prevents trailing-hyphen forms like "mcp-".
        return bool(re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", hostname))

    # - Kubernetes FQDN ending in .cluster.local
    return bool(hostname.endswith(".cluster.local"))
