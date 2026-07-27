"""
Tests for restock orders API endpoints (POST/GET /api/restock-orders).
"""
import pytest

# Restock orders are stored in an in-memory list that persists across requests
# within a process. Reset it before each test so tests stay independent.
from mock_data import restock_orders


@pytest.fixture(autouse=True)
def reset_restock_orders():
    """Clear the in-memory restock order store before each test."""
    restock_orders.clear()
    yield
    restock_orders.clear()


@pytest.fixture
def sample_restock_request():
    """A valid create-restock-order payload."""
    return {
        "budget": 50000,
        "items": [
            {"sku": "PCB-001", "name": "Single Layer PCB Assembly",
             "quantity": 10, "unit_price": 25.5, "trend": "increasing"},
            {"sku": "SEN-101", "name": "Temperature Sensor Module",
             "quantity": 5, "unit_price": 100.0, "trend": "decreasing"},
        ],
    }


class TestRestockOrderEndpoints:
    """Test suite for restock-order endpoints."""

    def test_get_restock_orders_empty(self, client):
        """Test getting restock orders when none have been submitted."""
        response = client.get("/api/restock-orders")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_create_restock_order(self, client, sample_restock_request):
        """Test submitting a valid restock order."""
        response = client.post("/api/restock-orders", json=sample_restock_request)
        assert response.status_code == 201

        order = response.json()
        # Server-generated fields
        assert order["order_number"] == "RESTOCK-2025-0001"
        assert order["status"] == "Submitted"
        assert order["budget"] == 50000
        assert "created_date" in order
        assert "T" in order["expected_delivery"]  # ISO datetime
        assert len(order["items"]) == 2

    def test_create_restock_order_computes_lead_times(self, client, sample_restock_request):
        """Test that per-item and overall lead times are derived from trend."""
        response = client.post("/api/restock-orders", json=sample_restock_request)
        assert response.status_code == 201

        order = response.json()
        lead_by_sku = {item["sku"]: item["lead_time_days"] for item in order["items"]}
        assert lead_by_sku["PCB-001"] == 5   # increasing -> 5 days (expedited)
        assert lead_by_sku["SEN-101"] == 14  # decreasing -> 14 days
        # Overall lead time is the slowest line item
        assert order["lead_time_days"] == 14

    def test_create_restock_order_unknown_trend_uses_default(self, client):
        """Test that an unrecognized trend falls back to the default lead time."""
        payload = {
            "budget": 1000,
            "items": [
                {"sku": "X-1", "name": "Mystery", "quantity": 2,
                 "unit_price": 10.0, "trend": "sideways"},
            ],
        }
        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 201
        assert response.json()["items"][0]["lead_time_days"] == 10  # DEFAULT_LEAD_TIME

    def test_create_restock_order_total_value_calculation(self, client, sample_restock_request):
        """Test that total_value equals sum(quantity * unit_price)."""
        response = client.post("/api/restock-orders", json=sample_restock_request)
        order = response.json()

        expected_total = sum(i["quantity"] * i["unit_price"] for i in order["items"])
        assert abs(order["total_value"] - expected_total) < 0.01
        assert order["total_value"] == 755.0  # 10*25.5 + 5*100

    def test_create_restock_order_empty_items_returns_400(self, client):
        """Test that submitting an order with no items is rejected."""
        response = client.post("/api/restock-orders", json={"budget": 100, "items": []})
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
        assert "no items" in data["detail"].lower()

    def test_submitted_order_appears_in_get(self, client, sample_restock_request):
        """Test that a submitted order is returned by the GET endpoint."""
        create_response = client.post("/api/restock-orders", json=sample_restock_request)
        created = create_response.json()

        list_response = client.get("/api/restock-orders")
        assert list_response.status_code == 200

        data = list_response.json()
        assert len(data) == 1
        assert data[0]["order_number"] == created["order_number"]

    def test_multiple_orders_newest_first_with_incrementing_numbers(self, client, sample_restock_request):
        """Test order numbers increment and GET returns newest first."""
        client.post("/api/restock-orders", json=sample_restock_request)
        client.post("/api/restock-orders", json=sample_restock_request)

        data = client.get("/api/restock-orders").json()
        assert len(data) == 2
        # Newest first
        assert data[0]["order_number"] == "RESTOCK-2025-0002"
        assert data[1]["order_number"] == "RESTOCK-2025-0001"

    def test_restock_order_item_structure(self, client, sample_restock_request):
        """Test that each restock order item has the expected fields/types."""
        response = client.post("/api/restock-orders", json=sample_restock_request)
        order = response.json()

        for item in order["items"]:
            assert "sku" in item
            assert "name" in item
            assert "trend" in item
            assert isinstance(item["quantity"], int)
            assert isinstance(item["unit_price"], (int, float))
            assert isinstance(item["lead_time_days"], int)
