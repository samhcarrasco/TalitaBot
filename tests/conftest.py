from unittest.mock import AsyncMock, patch

import pytest

from telegram import Bot


@pytest.fixture(autouse=True)
def disable_telegram_network():
    """Prevent real Telegram network calls during tests by mocking Bot's network methods."""
    with patch.object(Bot, "send_message", new_callable=AsyncMock):
        with patch.object(Bot, "send_photo", new_callable=AsyncMock):
            yield
