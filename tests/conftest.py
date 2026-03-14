"""Pytest configuration and shared fixtures for pointy-rag tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_conn():
    """Return a mock psycopg connection."""
    conn = MagicMock()
    conn.execute.return_value = conn
    return conn
