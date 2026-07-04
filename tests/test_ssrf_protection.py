"""Tests for SSRF protection utilities."""

from __future__ import annotations

import pytest

from mcp_authflow_resource.auth.ssrf_protection import is_safe_url


class TestIsSafeUrl:
    """Tests for is_safe_url."""

    def test_https_always_allowed(self) -> None:
        assert is_safe_url("https://example.com/introspect") is True

    def test_localhost_allowed_by_default(self) -> None:
        assert is_safe_url("http://localhost:9000/introspect") is True
        assert is_safe_url("http://127.0.0.1:9000/introspect") is True

    def test_localhost_rejected_when_disabled(self) -> None:
        assert is_safe_url("http://localhost:9000", allow_localhost=False) is False
        assert is_safe_url("http://localhost/introspect", allow_localhost=False) is False
        assert is_safe_url("http://127.0.0.1:9000", allow_localhost=False) is False

    def test_docker_single_segment_hostname(self) -> None:
        assert is_safe_url("http://mcp-auth:80/introspect") is True
        assert is_safe_url("http://backend/introspect") is True

    def test_k8s_cluster_local_fqdn(self) -> None:
        assert is_safe_url("http://mcp-auth.taskmanager.svc.cluster.local/introspect") is True
        assert is_safe_url("http://backend.default.svc.cluster.local:8080/api") is True

    def test_arbitrary_external_http_rejected(self) -> None:
        assert is_safe_url("http://evil.com/introspect") is False
        assert is_safe_url("http://example.com:8080/callback") is False

    def test_non_http_schemes_rejected(self) -> None:
        assert is_safe_url("file:///etc/passwd") is False
        assert is_safe_url("ftp://internal/file") is False

    @pytest.mark.parametrize(
        "url",
        [
            "http://mcp-auth.taskmanager.svc.cluster.local/introspect",
            "http://service.namespace.svc.cluster.local",
            "http://a.b.c.d.cluster.local:1234/path",
        ],
    )
    def test_cluster_local_variants(self, url: str) -> None:
        assert is_safe_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://cluster.local.evil.com/steal",
            "http://not-cluster.localx",
        ],
    )
    def test_cluster_local_lookalikes_rejected(self, url: str) -> None:
        assert is_safe_url(url) is False


class TestBypassProtection:
    """Tests covering previously-exploitable SSRF bypass techniques."""

    def test_ipv6_loopback_rejected_when_localhost_disabled(self) -> None:
        # ::1 is IPv6 loopback; must be rejected when allow_localhost=False
        assert is_safe_url("http://[::1]/", allow_localhost=False) is False

    def test_ipv6_loopback_allowed_when_localhost_enabled(self) -> None:
        # ::1 is loopback and allow_localhost=True, so it should be allowed
        assert is_safe_url("http://[::1]/", allow_localhost=True) is True

    def test_ipv6_non_loopback_rejected(self) -> None:
        # A globally-routable IPv6 address must always be rejected
        assert is_safe_url("http://[2001:db8::1]/") is False

    def test_userinfo_injection_external_host_rejected(self) -> None:
        # urlparse extracts hostname="evil.com"; correctly rejected by allowlist
        assert is_safe_url("http://user@evil.com/") is False

    def test_userinfo_injection_internal_host_preserved(self) -> None:
        # urlparse extracts hostname="mcp-auth"; this is a valid internal service name.
        # The attacker's intent was to reach evil.com but the parsed hostname is mcp-auth,
        # which IS on the allowlist — document that userinfo cannot inject a non-allowlisted
        # host, and the parsed result is correctly the safe internal hostname.
        assert is_safe_url("http://evil.com@mcp-auth/") is True

    def test_decimal_ip_without_localhost_rejected(self) -> None:
        # 2130706433 == 127.0.0.1 in decimal form.
        # ipaddress.ip_address("2130706433") raises ValueError in stdlib,
        # so this falls through to hostname checks. "2130706433" has no dot
        # and does not match [a-z][a-z0-9-]* (starts with digit), so rejected.
        assert is_safe_url("http://2130706433/", allow_localhost=False) is False

    def test_decimal_ip_with_localhost_also_rejected(self) -> None:
        # Even with allow_localhost=True, decimal IP "2130706433" is not parsed
        # as an IP by stdlib and fails the letter-prefix DNS label check.
        assert is_safe_url("http://2130706433/", allow_localhost=True) is False

    def test_null_hostname_rejected(self) -> None:
        # http:///path has no hostname component
        assert is_safe_url("http:///path") is False

    def test_empty_url_rejected(self) -> None:
        assert is_safe_url("") is False

    def test_url_with_only_scheme_rejected(self) -> None:
        # http:// has no hostname
        assert is_safe_url("http://") is False

    def test_hex_dotted_ip_rejected(self) -> None:
        # 0x7f.0x0.0x0.0x1 has dots but does not end in .cluster.local
        assert is_safe_url("http://0x7f.0x0.0x0.0x1/") is False

    def test_percent_encoded_hostname_rejected(self) -> None:
        # urlparse.hostname does NOT decode percent-encoding in Python 3.x —
        # the hostname stays as the literal encoded string (e.g. "%6c%6f%63%61...").
        # This encoded form fails all allowlist checks, so it is safely rejected
        # regardless of allow_localhost setting.
        assert is_safe_url("http://%6C%6F%63%61%6C%68%6F%73%74/", allow_localhost=True) is False
        assert is_safe_url("http://%6C%6F%63%61%6C%68%6F%73%74/", allow_localhost=False) is False

    def test_ipv4_mapped_ipv6_loopback_follows_loopback_rules(self) -> None:
        # ::ffff:127.0.0.1 is an IPv4-mapped IPv6 address for 127.0.0.1
        assert is_safe_url("http://[::ffff:127.0.0.1]/", allow_localhost=True) is True
        assert is_safe_url("http://[::ffff:127.0.0.1]/", allow_localhost=False) is False

    def test_ipv4_private_ip_rejected(self) -> None:
        # Private IPs other than loopback are not in the allowlist
        assert is_safe_url("http://192.168.1.1/") is False
        assert is_safe_url("http://10.0.0.1/") is False

    def test_urlparse_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # CPython's urlparse rarely raises, but the guard must fail closed if a
        # stricter future version (or unexpected input) makes it raise. Patch
        # urlparse to raise and assert the fallback returns False rather than
        # propagating the exception.
        def _raise(_url: str) -> object:
            raise ValueError("simulated urlparse failure")

        monkeypatch.setattr("mcp_authflow_resource.auth.ssrf_protection.urlparse", _raise)
        assert is_safe_url("anything") is False
