"""Unit tests for the Frame Extractor module.

Tests validate(), start/stop lifecycle, frame resizing, callback registration,
and reconnection backoff logic.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import cv2
import numpy as np
import pytest

from src.enums import PipelineStatus
from src.frame_extractor import (
    DEFAULT_SOURCE_FPS,
    ConnectionError,
    FrameExtractor,
    FrameExtractorConfig,
)


@pytest.fixture
def extractor() -> FrameExtractor:
    """Create a fresh FrameExtractor instance."""
    return FrameExtractor()


@pytest.fixture
def config() -> FrameExtractorConfig:
    """Create a default config for tests."""
    return FrameExtractorConfig(source_url="http://example.com/stream")


class TestParseSourceFps:
    """Tests for FrameExtractor._parse_source_fps FPS coercion/fallback."""

    def test_valid_fps_is_used(self) -> None:
        assert FrameExtractor._parse_source_fps(29.97) == pytest.approx(29.97)

    def test_integer_fps_is_used(self) -> None:
        assert FrameExtractor._parse_source_fps(30) == 30.0

    def test_zero_falls_back_to_default(self) -> None:
        assert FrameExtractor._parse_source_fps(0.0) == DEFAULT_SOURCE_FPS

    def test_negative_falls_back_to_default(self) -> None:
        assert FrameExtractor._parse_source_fps(-1.0) == DEFAULT_SOURCE_FPS

    def test_nan_falls_back_to_default(self) -> None:
        assert FrameExtractor._parse_source_fps(float("nan")) == DEFAULT_SOURCE_FPS

    def test_inf_falls_back_to_default(self) -> None:
        assert FrameExtractor._parse_source_fps(float("inf")) == DEFAULT_SOURCE_FPS

    def test_non_numeric_falls_back_to_default(self) -> None:
        assert FrameExtractor._parse_source_fps(object()) == DEFAULT_SOURCE_FPS


class TestFrameExtractorConfig:
    """Tests for FrameExtractorConfig defaults."""

    def test_default_skip_frames(self) -> None:
        config = FrameExtractorConfig(source_url="http://test.com/stream")
        assert config.skip_frames == 30

    def test_default_target_dimensions(self) -> None:
        config = FrameExtractorConfig(source_url="http://test.com/stream")
        assert config.target_width == 1920
        assert config.target_height == 1080

    def test_custom_values(self) -> None:
        config = FrameExtractorConfig(
            source_url="rtsp://camera/feed",
            skip_frames=10,
            target_width=1280,
            target_height=720,
        )
        assert config.source_url == "rtsp://camera/feed"
        assert config.skip_frames == 10
        assert config.target_width == 1280
        assert config.target_height == 720


class TestConnectionError:
    """Tests for ConnectionError dataclass."""

    def test_fields(self) -> None:
        err = ConnectionError(message="Not reachable", url="http://bad.url")
        assert err.message == "Not reachable"
        assert err.url == "http://bad.url"


class TestValidate:
    """Tests for FrameExtractor.validate()."""

    async def test_empty_url_returns_error(self, extractor: FrameExtractor) -> None:
        result = await extractor.validate("")
        assert isinstance(result, ConnectionError)
        assert "empty" in result.message.lower()

    async def test_whitespace_url_returns_error(self, extractor: FrameExtractor) -> None:
        result = await extractor.validate("   ")
        assert isinstance(result, ConnectionError)

    @patch("src.frame_extractor.cv2.VideoCapture")
    async def test_unreachable_url_returns_error(
        self, mock_cap_cls: MagicMock, extractor: FrameExtractor
    ) -> None:
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cap.release.return_value = None
        mock_cap_cls.return_value = mock_cap

        result = await extractor.validate("http://unreachable.example.com/stream")
        assert isinstance(result, ConnectionError)
        assert "unable to open" in result.message.lower() or "unreachable" in result.message.lower()

    @patch("src.frame_extractor.cv2.VideoCapture")
    async def test_opened_but_no_frames_returns_error(
        self, mock_cap_cls: MagicMock, extractor: FrameExtractor
    ) -> None:
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (False, None)
        mock_cap.release.return_value = None
        mock_cap_cls.return_value = mock_cap

        result = await extractor.validate("http://example.com/no-frames")
        assert isinstance(result, ConnectionError)
        assert "no frames" in result.message.lower() or "not be active" in result.message.lower()

    @patch("src.frame_extractor.cv2.VideoCapture")
    async def test_valid_source_returns_none(
        self, mock_cap_cls: MagicMock, extractor: FrameExtractor
    ) -> None:
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))
        mock_cap.release.return_value = None
        mock_cap_cls.return_value = mock_cap

        result = await extractor.validate("http://example.com/good-stream")
        assert result is None


class TestResizeFrame:
    """Tests for FrameExtractor.resize_frame()."""

    def test_resize_smaller_to_target(self, extractor: FrameExtractor) -> None:
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        resized = extractor.resize_frame(frame, 1920, 1080)
        assert resized.shape == (1080, 1920, 3)

    def test_resize_larger_to_target(self, extractor: FrameExtractor) -> None:
        frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
        resized = extractor.resize_frame(frame, 1920, 1080)
        assert resized.shape == (1080, 1920, 3)

    def test_resize_same_size(self, extractor: FrameExtractor) -> None:
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        resized = extractor.resize_frame(frame, 1920, 1080)
        assert resized.shape == (1080, 1920, 3)

    def test_resize_non_standard_aspect_ratio(self, extractor: FrameExtractor) -> None:
        frame = np.zeros((100, 300, 3), dtype=np.uint8)
        resized = extractor.resize_frame(frame, 1920, 1080)
        assert resized.shape == (1080, 1920, 3)

    def test_resize_preserves_dtype(self, extractor: FrameExtractor) -> None:
        frame = np.ones((720, 1280, 3), dtype=np.uint8) * 128
        resized = extractor.resize_frame(frame, 1920, 1080)
        assert resized.dtype == np.uint8

    def test_resize_custom_target(self, extractor: FrameExtractor) -> None:
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        resized = extractor.resize_frame(frame, 1280, 720)
        assert resized.shape == (720, 1280, 3)


class TestCallbackRegistration:
    """Tests for on_frame() and on_status_change() callback registration."""

    def test_register_frame_callback(self, extractor: FrameExtractor) -> None:
        callback = AsyncMock()
        extractor.on_frame(callback)
        assert callback in extractor._frame_callbacks

    def test_register_multiple_frame_callbacks(self, extractor: FrameExtractor) -> None:
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        extractor.on_frame(cb1)
        extractor.on_frame(cb2)
        assert len(extractor._frame_callbacks) == 2

    def test_register_status_callback(self, extractor: FrameExtractor) -> None:
        callback = AsyncMock()
        extractor.on_status_change(callback)
        assert callback in extractor._status_callbacks

    def test_register_multiple_status_callbacks(self, extractor: FrameExtractor) -> None:
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        extractor.on_status_change(cb1)
        extractor.on_status_change(cb2)
        assert len(extractor._status_callbacks) == 2


class TestStartStop:
    """Tests for start/stop lifecycle."""

    def test_initial_status_is_stopped(self, extractor: FrameExtractor) -> None:
        assert extractor.status == PipelineStatus.STOPPED

    def test_start_raises_if_already_running(
        self, extractor: FrameExtractor, config: FrameExtractorConfig
    ) -> None:
        # Mock the capture loop to prevent actual execution
        with patch.object(extractor, "_capture_loop", new_callable=AsyncMock):
            extractor.start(config)
            extractor._status = PipelineStatus.RUNNING
            with pytest.raises(RuntimeError, match="already running"):
                extractor.start(config)
            extractor.stop()

    async def test_stop_when_already_stopped(self, extractor: FrameExtractor) -> None:
        # Should not raise
        extractor.stop()
        assert extractor.status == PipelineStatus.STOPPED

    async def test_start_sets_config(
        self, extractor: FrameExtractor, config: FrameExtractorConfig
    ) -> None:
        with patch.object(extractor, "_capture_loop", new_callable=AsyncMock):
            extractor.start(config)
            assert extractor._config == config
            extractor.stop()

    async def test_start_resets_sequence_number(
        self, extractor: FrameExtractor, config: FrameExtractorConfig
    ) -> None:
        extractor._sequence_number = 42
        with patch.object(extractor, "_capture_loop", new_callable=AsyncMock):
            extractor.start(config)
            assert extractor._sequence_number == 0
            extractor.stop()


class TestCaptureLoop:
    """Tests for the async capture loop behavior."""

    @patch("src.frame_extractor.cv2.VideoCapture")
    async def test_capture_loop_produces_frames(
        self, mock_cap_cls: MagicMock, extractor: FrameExtractor
    ) -> None:
        """Test that capture loop reads, resizes, and delivers frames."""
        frame_640x480 = np.ones((480, 640, 3), dtype=np.uint8) * 100

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.set.return_value = True
        mock_cap.release.return_value = None
        # Return 3 frames, then disconnect
        mock_cap.read.side_effect = [
            (True, frame_640x480.copy()),
            (True, frame_640x480.copy()),
            (True, frame_640x480.copy()),
            (False, None),
        ]
        mock_cap_cls.return_value = mock_cap

        frame_callback = AsyncMock()
        status_callback = AsyncMock()
        extractor.on_frame(frame_callback)
        extractor.on_status_change(status_callback)

        config = FrameExtractorConfig(
            source_url="http://test.com/stream",
            skip_frames=1,  # Process every frame for test speed
        )

        extractor._config = config
        extractor._stop_event.clear()

        # Run capture loop with a timeout to prevent infinite wait
        # We patch _reconnect_with_backoff to immediately return False (simulating disconnect)
        with patch.object(extractor, "_reconnect_with_backoff", new_callable=AsyncMock) as mock_reconnect:
            mock_reconnect.return_value = False
            await extractor._capture_loop()

        # Should have received 3 frames
        assert frame_callback.call_count == 3

        # All frames should be resized to 1920x1080
        for call in frame_callback.call_args_list:
            frame_arg = call[0][0]
            assert frame_arg.shape == (1080, 1920, 3)

        # Sequence numbers should be 1, 2, 3
        seq_nums = [call[0][1] for call in frame_callback.call_args_list]
        assert seq_nums == [1, 2, 3]

        # Absolute source frame numbers are the 0-based read indices (skip=1)
        frame_numbers = [call[0][2] for call in frame_callback.call_args_list]
        assert frame_numbers == [0, 1, 2]

        # Source FPS is delivered as the 4th positional arg and is positive
        source_fps_values = [call[0][3] for call in frame_callback.call_args_list]
        assert all(fps > 0 for fps in source_fps_values)

    @patch("src.frame_extractor.cv2.VideoCapture")
    async def test_capture_loop_transitions_to_running(
        self, mock_cap_cls: MagicMock, extractor: FrameExtractor
    ) -> None:
        """Test that starting the loop transitions status to RUNNING."""
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (False, None)
        mock_cap.release.return_value = None
        mock_cap_cls.return_value = mock_cap

        status_callback = AsyncMock()
        extractor.on_status_change(status_callback)

        config = FrameExtractorConfig(
            source_url="http://test.com/stream", skip_frames=1
        )
        extractor._config = config
        extractor._stop_event.clear()

        with patch.object(extractor, "_reconnect_with_backoff", new_callable=AsyncMock) as mock_reconnect:
            mock_reconnect.return_value = False
            await extractor._capture_loop()

        # First status change should be to RUNNING
        first_call_status = status_callback.call_args_list[0][0][0]
        assert first_call_status == PipelineStatus.RUNNING


class TestReconnectionBackoff:
    """Tests for exponential backoff reconnection logic."""

    def test_backoff_constants(self) -> None:
        assert FrameExtractor._INITIAL_BACKOFF_S == 1.0
        assert FrameExtractor._MAX_BACKOFF_S == 30.0
        assert FrameExtractor._BACKOFF_MULTIPLIER == 2.0

    @patch("src.frame_extractor.cv2.VideoCapture")
    async def test_reconnect_succeeds(
        self, mock_cap_cls: MagicMock, extractor: FrameExtractor
    ) -> None:
        """Test successful reconnection after one retry."""
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))
        mock_cap.release.return_value = None
        mock_cap_cls.return_value = mock_cap

        status_callback = AsyncMock()
        extractor.on_status_change(status_callback)

        config = FrameExtractorConfig(source_url="http://test.com/stream")
        extractor._config = config
        extractor._stop_event.clear()

        # Patch asyncio.wait_for to speed up backoff wait
        with patch("src.frame_extractor.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await extractor._reconnect_with_backoff()

        assert result is True

    @patch("src.frame_extractor.cv2.VideoCapture")
    async def test_reconnect_fails_when_stopped(
        self, mock_cap_cls: MagicMock, extractor: FrameExtractor
    ) -> None:
        """Test that reconnection aborts if stop is requested."""
        config = FrameExtractorConfig(source_url="http://test.com/stream")
        extractor._config = config
        extractor._stop_event.set()  # Signal stop immediately

        result = await extractor._reconnect_with_backoff()
        assert result is False

    async def test_reconnect_transitions_to_reconnecting(
        self, extractor: FrameExtractor
    ) -> None:
        """Test that reconnection sets RECONNECTING status."""
        status_callback = AsyncMock()
        extractor.on_status_change(status_callback)

        config = FrameExtractorConfig(source_url="http://test.com/stream")
        extractor._config = config
        extractor._stop_event.set()  # Will exit immediately

        await extractor._reconnect_with_backoff()

        # Should have transitioned to RECONNECTING
        status_callback.assert_any_call(PipelineStatus.RECONNECTING)


# --- Property-Based Tests ---

from hypothesis import given, settings
from hypothesis import strategies as st


class TestFrameResizeProperty:
    """Property-based tests for FrameExtractor.resize_frame().

    **Validates: Requirements 2.8**
    """

    @given(
        width=st.integers(min_value=1, max_value=4000),
        height=st.integers(min_value=1, max_value=4000),
    )
    @settings(max_examples=200)
    def test_property_frame_resize_invariant(self, width: int, height: int) -> None:
        """Property 3: Frame Resize Invariant

        For any input frame of arbitrary resolution (width × height), the
        Frame Extractor SHALL produce an output frame with dimensions exactly
        1920×1080 pixels.

        **Validates: Requirements 2.8**
        """
        extractor = FrameExtractor()

        # Generate a random BGR frame with the given dimensions
        frame = np.zeros((height, width, 3), dtype=np.uint8)

        resized = extractor.resize_frame(frame, 1920, 1080)

        assert resized.shape == (1080, 1920, 3), (
            f"Expected shape (1080, 1920, 3), got {resized.shape} "
            f"for input frame of size ({height}, {width}, 3)"
        )
        assert resized.dtype == np.uint8
