"""Pytest fixtures."""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_client_session():
    """Fixture to mock aiohttp.ClientSession."""
    session = MagicMock()
    session.get = AsyncMock()
    session.post = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    yield


@pytest.fixture
def mock_config_entry():
    """Fixture to provide a mock ConfigEntry."""
    config_entry = MagicMock()
    config_entry.data = {
        "host": "192.168.1.100",
        "username": "test_user",
        "password": "test_password",
    }
    config_entry.entry_id = "test_entry_id"
    config_entry.options = {}
    return config_entry


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Stop setup entry from failing if no config entry is initialized."""
    from unittest.mock import patch

    with patch("custom_components.innotemp.async_setup_entry", return_value=True):
        yield
