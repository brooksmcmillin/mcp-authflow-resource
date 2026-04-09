"""Pytest configuration and fixtures for mcp-resource-framework tests."""

from __future__ import annotations

import logging
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def reset_agent_framework_logger_propagation() -> Generator[None, None, None]:
    """Restore agent_framework logger propagation state between tests.

    agent_framework.logging.setup_logging() sets ``agent_framework.propagate =
    False`` so that production agents don't double-emit to root.  When those
    tests run first in the full suite, the flag is left as ``False``, causing
    log records from ``agent_framework.security.lakera_guard`` to bypass pytest's
    caplog handler (which is attached to the root logger).  This fixture saves
    and restores the propagation flag and explicit level so the caplog-based
    tests in ``TestFailOpenConfig`` are order-independent.
    """
    af_logger = logging.getLogger("agent_framework")
    original_propagate = af_logger.propagate
    original_level = af_logger.level

    # Ensure propagation is enabled so caplog (root handler) captures log records.
    af_logger.propagate = True

    yield

    af_logger.propagate = original_propagate
    af_logger.level = original_level
