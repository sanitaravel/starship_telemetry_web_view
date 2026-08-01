"""Tests for the FastAPI WebSocket server and REST endpoints.

Verifies:
- /api/status endpoint returns correct shape
- /api/validate-url endpoint handles empty/invalid URLs
- WebSocket connection can be established and receives messages
"""

import pytest
from unittest.mock import patch, AsyncMock

from httpx import AsyncClient, ASGITransport

from src.server import create_app, ConnectionManager, manager


@pytest.fixture
def app():
    """Create a fresh FastAPI app instance for testing."""
    return create_app()


@pytest.fixture
async def client(app):
    """Create an async HTTP test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestGetPipelineStatus:
    """Tests for GET /api/status endpoint."""

    async def test_status_returns_correct_shape(self, client: AsyncClient):
        """Status response should contain status, gpu, frame_interval_ms, current_sequence."""
        response = await client.get("/api/status")
        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "gpu" in data
        assert "frame_interval_ms" in data
        assert "current_sequence" in data

    async def test_status_gpu_has_required_fields(self, client: AsyncClient):
        """GPU info should have 'available' and 'device_name' fields."""
        response = await client.get("/api/status")
        data = response.json()

        gpu = data["gpu"]
        assert "available" in gpu
        assert "device_name" in gpu
        assert isinstance(gpu["available"], bool)

    async def test_status_values_are_correct_types(self, client: AsyncClient):
        """Status fields should have expected types."""
        response = await client.get("/api/status")
        data = response.json()

        assert isinstance(data["status"], str)
        assert data["status"] in ("stopped", "running", "disconnected", "reconnecting")
        assert isinstance(data["frame_interval_ms"], int)
        assert isinstance(data["current_sequence"], int)

    async def test_initial_status_is_stopped(self, client: AsyncClient):
        """Initial pipeline status should be 'stopped'."""
        response = await client.get("/api/status")
        data = response.json()
        assert data["status"] == "stopped"


class TestValidateUrl:
    """Tests for POST /api/validate-url endpoint."""

    async def test_empty_url_returns_invalid(self, client: AsyncClient):
        """An empty URL should return valid=False."""
        response = await client.post("/api/validate-url", json={"url": ""})
        assert response.status_code == 200
        data = response.json()

        assert data["valid"] is False
        assert "empty" in data["message"].lower() or "cannot" in data["message"].lower()
        assert data["url"] == ""

    async def test_whitespace_url_returns_invalid(self, client: AsyncClient):
        """A whitespace-only URL should return valid=False."""
        response = await client.post("/api/validate-url", json={"url": "   "})
        assert response.status_code == 200
        data = response.json()

        assert data["valid"] is False

    async def test_unreachable_url_returns_invalid(self, client: AsyncClient):
        """An unreachable URL should return valid=False with a message."""
        response = await client.post(
            "/api/validate-url",
            json={"url": "rtsp://nonexistent.invalid/stream"},
        )
        assert response.status_code == 200
        data = response.json()

        assert data["valid"] is False
        assert data["url"] == "rtsp://nonexistent.invalid/stream"
        assert len(data["message"]) > 0

    async def test_response_has_correct_shape(self, client: AsyncClient):
        """Response should always contain valid, message, and url."""
        response = await client.post(
            "/api/validate-url", json={"url": "http://example.com/stream"}
        )
        assert response.status_code == 200
        data = response.json()

        assert "valid" in data
        assert "message" in data
        assert "url" in data
        assert isinstance(data["valid"], bool)
        assert isinstance(data["message"], str)
        assert isinstance(data["url"], str)

    async def test_missing_url_field_returns_422(self, client: AsyncClient):
        """A request without the 'url' field should return 422 validation error."""
        response = await client.post("/api/validate-url", json={})
        assert response.status_code == 422


class TestConnectionManager:
    """Tests for the WebSocket ConnectionManager class."""

    def test_initial_state_has_no_connections(self):
        """A new ConnectionManager should start with zero connections."""
        cm = ConnectionManager()
        assert len(cm.active_connections) == 0

    async def test_disconnect_removes_from_list(self):
        """Disconnecting a WebSocket should remove it from active_connections."""
        cm = ConnectionManager()

        # Create a mock websocket
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()

        await cm.connect(mock_ws)
        assert len(cm.active_connections) == 1

        cm.disconnect(mock_ws)
        assert len(cm.active_connections) == 0

    async def test_disconnect_nonexistent_is_safe(self):
        """Disconnecting a WebSocket not in the list should not raise."""
        cm = ConnectionManager()
        mock_ws = AsyncMock()
        # Should not raise
        cm.disconnect(mock_ws)
        assert len(cm.active_connections) == 0

    async def test_broadcast_sends_to_all_connected(self):
        """Broadcasting should send to all connected clients."""
        cm = ConnectionManager()

        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_text = AsyncMock()

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_text = AsyncMock()

        await cm.connect(ws1)
        await cm.connect(ws2)

        await cm.broadcast("test message")

        ws1.send_text.assert_called_once_with("test message")
        ws2.send_text.assert_called_once_with("test message")

    async def test_broadcast_removes_failed_connections(self):
        """Broadcasting should remove clients that fail to receive."""
        cm = ConnectionManager()

        ws_good = AsyncMock()
        ws_good.accept = AsyncMock()
        ws_good.send_text = AsyncMock()

        ws_bad = AsyncMock()
        ws_bad.accept = AsyncMock()
        ws_bad.send_text = AsyncMock(side_effect=Exception("Connection closed"))

        await cm.connect(ws_good)
        await cm.connect(ws_bad)
        assert len(cm.active_connections) == 2

        await cm.broadcast("test")

        # ws_bad should have been removed
        assert len(cm.active_connections) == 1
        assert ws_good in cm.active_connections

    async def test_broadcast_json_sends_to_all(self):
        """broadcast_json should send JSON data to all clients."""
        cm = ConnectionManager()

        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()

        await cm.connect(ws1)
        await cm.broadcast_json({"type": "test", "payload": "data"})

        ws1.send_json.assert_called_once_with({"type": "test", "payload": "data"})


class TestWebSocketEndpoint:
    """Tests for the /ws/telemetry WebSocket endpoint."""

    async def test_websocket_connection_accepted(self, app):
        """WebSocket connection should be accepted at /ws/telemetry."""
        from httpx_ws import aconnect_ws
        from httpx_ws.transport import ASGIWebSocketTransport

        async with AsyncClient(
            transport=ASGIWebSocketTransport(app=app), base_url="http://test"
        ) as client:
            async with aconnect_ws("/ws/telemetry", client) as ws:
                # Connection was accepted - send a stop command to test communication
                await ws.send_json({"action": "stop"})
                response = await ws.receive_json()
                assert response["type"] == "status"
                assert "payload" in response

    async def test_websocket_unknown_action_returns_error(self, app):
        """Unknown actions should return an error message."""
        from httpx_ws import aconnect_ws
        from httpx_ws.transport import ASGIWebSocketTransport

        async with AsyncClient(
            transport=ASGIWebSocketTransport(app=app), base_url="http://test"
        ) as client:
            async with aconnect_ws("/ws/telemetry", client) as ws:
                await ws.send_json({"action": "unknown_action"})
                response = await ws.receive_json()
                assert response["type"] == "error"
                assert "unknown" in response["payload"]["message"].lower()

    async def test_websocket_validate_url_command(self, app):
        """validate_url action should return validation result."""
        from httpx_ws import aconnect_ws
        from httpx_ws.transport import ASGIWebSocketTransport

        async with AsyncClient(
            transport=ASGIWebSocketTransport(app=app), base_url="http://test"
        ) as client:
            async with aconnect_ws("/ws/telemetry", client) as ws:
                await ws.send_json({"action": "validate_url", "url": ""})
                response = await ws.receive_json()
                assert response["type"] == "validation_result"
                assert response["payload"]["valid"] is False
