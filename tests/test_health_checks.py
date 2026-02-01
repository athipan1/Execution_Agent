from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

def test_health_check_alpaca_success():
    """
    Test the /health/alpaca endpoint when the connection is successful.
    """
    with patch("app.main.AlpacaAdapter.check_connection") as mock_check:
        mock_check.return_value = True
        with TestClient(app) as client:
            response = client.get("/health/alpaca")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["data"]["status"] == "healthy"
            mock_check.assert_called_once()

def test_health_check_alpaca_failure():
    """
    Test the /health/alpaca endpoint when the connection fails.
    """
    with patch("app.main.AlpacaAdapter.check_connection") as mock_check:
        mock_check.return_value = False
        with TestClient(app) as client:
            response = client.get("/health/alpaca")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "error"
            assert data["error"]["message"] == "Could not connect to Alpaca."
            mock_check.assert_called_once()
