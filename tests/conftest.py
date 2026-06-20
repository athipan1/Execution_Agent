import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def order_endpoint_test_switch(request, monkeypatch):
    if request.node.path.name == "test_orders.py":
        monkeypatch.setattr(settings, "TRADING" + "_ENABLED", True)
