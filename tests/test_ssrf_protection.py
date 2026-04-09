"""Tests for SSRF protection utilities."""

from __future__ import annotations

import pytest

from mcp_resource_framework.auth.ssrf_protection import is_safe_url


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
