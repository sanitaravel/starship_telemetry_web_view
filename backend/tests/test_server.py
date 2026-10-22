"""Tests for the FastAPI WebSocket server and REST endpoints.

Verifies:
- /api/status endpoint returns correct shape
- /api/validate-url endpoint handles empty/invalid URLs
- WebSocket connection can be established and receives messages
"""

import json

import pytest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient
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
        """Status response should contain status, gpu, skip_frames, current_sequence."""
        response = await client.get("/api/status")
        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "gpu" in data
        assert "skip_frames" in data
        assert "current_sequence" in data
        assert "version" in data

    async def test_status_reports_backend_version(self, client: AsyncClient):
        """The status carries the running backend's version (see src/version.py)."""
        from src.version import get_version

        data = (await client.get("/api/status")).json()
        assert data["version"] == get_version()

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
        assert isinstance(data["skip_frames"], int)
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
        ws1.send_text = AsyncMock()
        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_text = AsyncMock()

        await cm.connect(ws1)
        await cm.connect(ws2)
        await cm.broadcast_json({"type": "test", "payload": "data"})

        # Serialised once, then sent as text to every client
        for ws in (ws1, ws2):
            ws.send_text.assert_called_once()
            assert json.loads(ws.send_text.call_args.args[0]) == {
                "type": "test",
                "payload": "data",
            }


class TestWebSocketEndpoint:
    """Tests for the /ws/telemetry WebSocket endpoint."""

    def test_websocket_connection_accepted(self, app):
        """WebSocket connection should be accepted at /ws/telemetry."""
        with TestClient(app) as client, client.websocket_connect("/ws/telemetry") as ws:
            # Connection was accepted - send a stop command to test communication
            ws.send_json({"action": "stop"})
            response = ws.receive_json()
            assert response["type"] == "status"
            assert "payload" in response

    def test_websocket_unknown_action_returns_error(self, app):
        """Unknown actions should return an error message."""
        with TestClient(app) as client, client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json({"action": "unknown_action"})
            response = ws.receive_json()
            assert response["type"] == "error"
            assert "unknown" in response["payload"]["message"].lower()

    def test_websocket_validate_url_command(self, app):
        """validate_url action should return validation result."""
        with TestClient(app) as client, client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json({"action": "validate_url", "url": ""})
            response = ws.receive_json()
            assert response["type"] == "validation_result"
            assert response["payload"]["valid"] is False


class TestWebSocketDispatch:
    """The action dispatch table, status broadcasts and disconnect cleanup."""

    @pytest.mark.parametrize(
        "action, expected_message",
        [
            ("warp_drive", "Unknown action: warp_drive"),
            (["stop"], "Unknown action: ['stop']"),
            ({"name": "stop"}, "Unknown action: {'name': 'stop'}"),
            (None, "Unknown action: None"),
            (42, "Unknown action: 42"),
        ],
        ids=["unknown-string", "list", "dict", "missing", "int"],
    )
    def test_unknown_or_non_string_action_errors_and_connection_survives(
        self, app, action, expected_message
    ):
        """Unknown/non-string actions get an error reply; the session keeps working."""
        message = {} if action is None else {"action": action}
        with TestClient(app) as client, client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json(message)
            assert ws.receive_json() == {
                "type": "error",
                "payload": {"message": expected_message},
            }

            # Still usable: a real command is handled normally afterwards
            ws.send_json({"action": "validate_url", "url": ""})
            response = ws.receive_json()
            assert response["type"] == "validation_result"

    def test_stop_broadcasts_status(self, app):
        """stop replies to every client with a status message."""
        with patch(
            "src.server.orchestrator.stop", new_callable=AsyncMock
        ) as stop, TestClient(app) as client, \
                client.websocket_connect("/ws/telemetry") as ws, \
                client.websocket_connect("/ws/telemetry") as other:
            ws.send_json({"action": "stop"})
            for conn in (ws, other):
                response = conn.receive_json()
                assert response["type"] == "status"
                assert response["payload"]["status"] == "stopped"
            stop.assert_awaited_once()

    def test_set_interval_broadcasts_status_with_new_skip(self, app):
        """set_interval updates skip_frames and broadcasts it to every client."""
        from src.server import orchestrator

        original = orchestrator.state.skip_frames
        try:
            with TestClient(app) as client, \
                    client.websocket_connect("/ws/telemetry") as ws, \
                    client.websocket_connect("/ws/telemetry") as other:
                ws.send_json({"action": "set_interval", "skip_frames": 12})
                for conn in (ws, other):
                    response = conn.receive_json()
                    assert response["type"] == "status"
                    assert response["payload"]["skip_frames"] == 12
        finally:
            orchestrator.set_skip_frames(original)

    def test_disconnect_removes_connection_from_manager(self, app):
        """Closing the socket runs the finally cleanup and unregisters it."""
        connected = []
        real_connect = manager.connect

        async def spy_connect(websocket):
            connected.append(websocket)
            await real_connect(websocket)

        before = len(manager.active_connections)
        with patch.object(manager, "connect", side_effect=spy_connect), \
                TestClient(app) as client:
            with client.websocket_connect("/ws/telemetry") as ws:
                # Round-trip so the server has definitely registered the socket
                ws.send_json({"action": "validate_url", "url": ""})
                ws.receive_json()
                assert len(connected) == 1
                assert connected[0] in manager.active_connections
                assert len(manager.active_connections) == before + 1

        assert connected[0] not in manager.active_connections
        assert len(manager.active_connections) == before

    def test_disconnect_logged_once(self, app, src_caplog):
        """The session-disconnected log fires exactly once per session."""
        import logging

        with src_caplog.at_level(logging.INFO, logger="src.server"), TestClient(app) as client:
            with client.websocket_connect("/ws/telemetry") as ws:
                ws.send_json({"action": "validate_url", "url": ""})
                ws.receive_json()
        disconnects = [r for r in src_caplog.records
                       if r.getMessage() == "WebSocket session disconnected"]
        assert len(disconnects) == 1
        assert not [r for r in src_caplog.records if r.getMessage().startswith("WebSocket error")]
