import respx
from fastapi.testclient import TestClient
from httpx import Response
from app.main import app
from app.config import settings

# Use respx to mock out all external connections
@respx.mock
def test_health_check_alpaca_full_lifecycle_success():
    """
    Test the /health/alpaca endpoint for a successful order placement and cancellation.
    """
    order_id = "test-order-id-123"

    # Mock the order placement request
    place_order_route = respx.post(f"{settings.ALPACA_API_URL}/v2/orders")
    place_order_route.return_value = Response(200, json={"id": order_id, "status": "new"})

    # Mock the order cancellation request
    cancel_order_route = respx.delete(f"{settings.ALPACA_API_URL}/v2/orders/{order_id}")
    cancel_order_route.return_value = Response(204)

    with TestClient(app) as client:
        response = client.get("/health/alpaca")

    assert response.status_code == 200
    expected_response = {
        "status": "ok",
        "order_id": order_id,
        "creation": "success",
        "cancellation": "success",
    }
    assert response.json() == expected_response
    assert place_order_route.called
    assert cancel_order_route.called


@respx.mock
def test_health_check_alpaca_creation_fails():
    """
    Test the /health/alpaca endpoint when order creation fails.
    """
    # Mock the order placement request to return an error
    place_order_route = respx.post(f"{settings.ALPACA_API_URL}/v2/orders")
    place_order_route.return_value = Response(403, json={"message": "forbidden"})

    with TestClient(app) as client:
        response = client.get("/health/alpaca")

    assert response.status_code == 503
    expected_detail = {
        "status": "error",
        "creation": "failed",
        "cancellation": "skipped",
            "reason": '{"message":"forbidden"}',
    }
    assert response.json()["detail"] == expected_detail
    assert place_order_route.called
