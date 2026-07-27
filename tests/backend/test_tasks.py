"""
Tests for tasks API endpoints (GET/POST/PATCH/DELETE /api/tasks).
"""
import pytest

# Tasks are stored in an in-memory list that persists across requests within a
# process. Reset it before each test so tests stay independent.
from mock_data import tasks, TASK_ID_START


@pytest.fixture(autouse=True)
def reset_tasks():
    """Clear the in-memory task store before and after each test."""
    tasks.clear()
    yield
    tasks.clear()


@pytest.fixture
def sample_task_request():
    """A valid create-task payload."""
    return {
        "title": "Review Q4 inventory levels",
        "priority": "high",
        "dueDate": "2026-08-15",
    }


class TestTaskEndpoints:
    """Test suite for task endpoints."""

    def test_get_tasks_empty(self, client):
        """Test getting tasks when none have been created."""
        response = client.get("/api/tasks")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_create_task(self, client, sample_task_request):
        """Test creating a valid task."""
        response = client.post("/api/tasks", json=sample_task_request)
        assert response.status_code == 201

        task = response.json()
        assert task["title"] == "Review Q4 inventory levels"
        assert task["priority"] == "high"
        assert task["dueDate"] == "2026-08-15"
        # New tasks always start pending
        assert task["status"] == "pending"
        # First API-generated id starts at TASK_ID_START to avoid colliding
        # with the client's static mock task ids (1-4).
        assert task["id"] == TASK_ID_START

    def test_create_task_trims_title(self, client):
        """Test that surrounding whitespace is stripped from the title."""
        response = client.post(
            "/api/tasks",
            json={"title": "  Padded title  ", "priority": "low", "dueDate": "2026-09-01"},
        )
        assert response.status_code == 201
        assert response.json()["title"] == "Padded title"

    def test_create_task_empty_title_returns_400(self, client):
        """Test that a blank/whitespace-only title is rejected."""
        response = client.post(
            "/api/tasks",
            json={"title": "   ", "priority": "low", "dueDate": "2026-09-01"},
        )
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
        assert "empty" in data["detail"].lower()

    def test_create_task_missing_field_returns_422(self, client):
        """Test that omitting a required field fails Pydantic validation."""
        response = client.post("/api/tasks", json={"title": "No due date"})
        assert response.status_code == 422

    def test_created_task_appears_in_get(self, client, sample_task_request):
        """Test that a created task is returned by the GET endpoint."""
        created = client.post("/api/tasks", json=sample_task_request).json()

        data = client.get("/api/tasks").json()
        assert len(data) == 1
        assert data[0]["id"] == created["id"]

    def test_multiple_tasks_newest_first_with_incrementing_ids(self, client, sample_task_request):
        """Test ids increment and GET returns newest first."""
        first = client.post("/api/tasks", json=sample_task_request).json()
        second = client.post("/api/tasks", json=sample_task_request).json()

        assert second["id"] == first["id"] + 1

        data = client.get("/api/tasks").json()
        assert len(data) == 2
        # Newest first
        assert data[0]["id"] == second["id"]
        assert data[1]["id"] == first["id"]

    def test_new_id_never_collides_with_live_task_after_deletion(self, client, sample_task_request):
        """Test that a new task's id can't collide with a still-live task.

        Deriving the id from max(existing) (not list length) is what guarantees
        this: after deleting the *lower* task, a length-based id would reuse the
        id of the still-live newer task.
        """
        first = client.post("/api/tasks", json=sample_task_request).json()
        second = client.post("/api/tasks", json=sample_task_request).json()

        # Delete the older task, leaving the newer one live, then create another.
        # Length-based: len is back to 1 -> would reuse second's id (collision).
        # Max-based: max(second) + 1 -> distinct from second.
        client.delete(f"/api/tasks/{first['id']}")
        third = client.post("/api/tasks", json=sample_task_request).json()

        assert third["id"] == second["id"] + 1
        assert third["id"] != second["id"]

    def test_toggle_task(self, client, sample_task_request):
        """Test toggling a task between pending and completed."""
        task_id = client.post("/api/tasks", json=sample_task_request).json()["id"]

        # pending -> completed
        response = client.patch(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "completed"

        # completed -> pending
        response = client.patch(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "pending"

    def test_toggle_nonexistent_task_returns_404(self, client):
        """Test that toggling a missing task returns 404."""
        response = client.patch("/api/tasks/999999")
        assert response.status_code == 404

        data = response.json()
        assert "detail" in data
        assert "not found" in data["detail"].lower()

    def test_delete_task(self, client, sample_task_request):
        """Test deleting a task removes it from the store."""
        task_id = client.post("/api/tasks", json=sample_task_request).json()["id"]

        response = client.delete(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["id"] == task_id

        # It should no longer be returned
        assert client.get("/api/tasks").json() == []

    def test_delete_nonexistent_task_returns_404(self, client):
        """Test that deleting a missing task returns 404."""
        response = client.delete("/api/tasks/999999")
        assert response.status_code == 404

        data = response.json()
        assert "detail" in data
        assert "not found" in data["detail"].lower()

    def test_task_structure(self, client, sample_task_request):
        """Test that a task has the expected fields and types."""
        task = client.post("/api/tasks", json=sample_task_request).json()

        assert isinstance(task["id"], int)
        assert isinstance(task["title"], str)
        assert isinstance(task["priority"], str)
        assert isinstance(task["dueDate"], str)
        assert task["status"] in ("pending", "completed")
