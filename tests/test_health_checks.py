from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


def test_health_check_is_lightweight_liveness_probe():
    """Docker healthcheck should not instantiate broker adapters."""
    with patch("app.main.get_broker_adapter") as mock_get_broker_adapter:
        with TestClient(app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "healthy"
    assert data["data"]["broker_connected"] is False
    mock_get_broker_adapter.assert_not_called()


def test_broker_health_check_reports_adapter_connection_success():
    adapter = AsyncMock()
    adapter.check_connection.return_value = True

    app.dependency_overrides.clear()
    app.dependency_overrides[__import__("app.main", fromlist=["get_broker_adapter"]).get_broker_adapter] = lambda: adapter
    try:
        with TestClient(app) as client:
            response = client.get("/health/broker")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "healthy"
    assert data["data"]["broker_connected"] is True
    adapter.check_connection.assert_awaited_once()


def test_alpaca_health_check_success():
    adapter = AsyncMock()
    adapter.get_account.return_value = {
        "paper": True,
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
    }

    with patch("app.main.AlpacaAdapter", return_value=adapter):
        with TestClient(app) as client:
            response = client.get("/health/alpaca")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["connected"] is True
    assert data["data"]["broker"] == "ALPACA"
    assert data["data"]["paper"] is True
    adapter.get_account.assert_awaited_once()


def test_alpaca_health_check_failure_returns_error_payload():
    adapter = AsyncMock()
    adapter.get_account.side_effect = RuntimeError("missing credentials")

    with patch("app.main.AlpacaAdapter", return_value=adapter):
        with TestClient(app) as client:
            response = client.get("/health/alpaca")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["data"]["connected"] is False
    assert data["error"]["message"] == "missing credentials"
    adapter.get_account.assert_awaited_once()
