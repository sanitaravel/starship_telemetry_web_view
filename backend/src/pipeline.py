"""Pipeline Orchestrator - wires all telemetry extraction components together.

Manages the full processing pipeline:
Frame Extractor → Engine Analyzer → OCR → Stage Assignment → Record Assembly → WebSocket broadcast.

Handles Start/Stop control commands and manages PipelineState including
status transitions, current template, and sequence counter.
"""

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import cv2
import numpy as np

from src.engine_analyzer import EngineAnalyzerConfig, analyze_engines
from src.enums import PipelineStatus, SeparationState
from src.frame_extractor import FrameExtractor, FrameExtractorConfig
from src.gpu_detector import GPUCapabilities
from src.models import ROIConfiguration
from src.ocr_engine import EasyOCREngine
from src.record_assembler import RecordAssembler
from src.stage_assignment import StageAssigner
from src.template_registry import TemplateRegistry, TemplateNotFoundError


logger = logging.getLogger(__name__)


@dataclass
class PipelineState:
    """Tracks the current state of the extraction pipeline."""

    status: PipelineStatus = PipelineStatus.STOPPED
    source_url: str | None = None
    source_validated: bool = False
    skip_frames: int = 30
    current_sequence: int = 0
    separation_state: SeparationState = SeparationState.PRE_SEPARATION
    active_template_name: str = "starship_rois"
    gpu_capabilities: GPUCapabilities | None = None
    processing_fps: float = 0.0  # actual frames processed per second


class PipelineOrchestrator:
    """Orchestrates the telemetry extraction pipeline.

    Wires together Frame Extractor, Engine Analyzer, OCR Engine,
    Stage Assigner, and Record Assembler. Manages pipeline lifecycle
    (start/stop) and broadcasts results to connected WebSocket clients.
    """

    def __init__(
        self,
        gpu_capabilities: GPUCapabilities,
        template_registry: TemplateRegistry,
        broadcast: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Initialize the pipeline orchestrator.

        Args:
            gpu_capabilities: Detected GPU capabilities for OCR acceleration.
            template_registry: Registry of ROI configuration templates.
            broadcast: Async callback to broadcast JSON messages to all
                connected WebSocket clients.
        """
        self._gpu_capabilities = gpu_capabilities
        self._template_registry = template_registry
        self._broadcast = broadcast

        # Pipeline components
        self._frame_extractor = FrameExtractor()
        self._ocr_engine: EasyOCREngine | None = None  # Lazy init (expensive)
        self._stage_assigner = StageAssigner()
        self._record_assembler = RecordAssembler()
        self._engine_analyzer_config = EngineAnalyzerConfig()

        # Pipeline state
        self._state = PipelineState(gpu_capabilities=gpu_capabilities)

        # FPS tracking
        self._last_frame_time: float = 0.0
        self._fps_samples: list[float] = []  # recent frame durations for averaging

        # Active ROI configuration (resolved from template on start)
        self._roi_config: ROIConfiguration | None = None

        # Register callbacks on frame extractor
        self._frame_extractor.on_frame(self._on_frame)
        self._frame_extractor.on_status_change(self._on_status_change)

    @property
    def state(self) -> PipelineState:
        """Return the current pipeline state."""
        return self._state

    @property
    def frame_extractor(self) -> FrameExtractor:
        """Return the frame extractor instance (for URL validation)."""
        return self._frame_extractor

    def _ensure_ocr_engine(self) -> EasyOCREngine:
        """Lazily initialize the OCR engine (expensive neural net load).

        Returns:
            The initialized EasyOCREngine instance.
        """
        if self._ocr_engine is None:
            logger.info("Initializing EasyOCR engine (this may take a moment)...")
            self._ocr_engine = EasyOCREngine(self._gpu_capabilities)
            logger.info("EasyOCR engine initialized.")
        return self._ocr_engine

    async def start(self, source_url: str, skip_frames: int = 30) -> None:
        """Start the extraction pipeline.

        Resolves the active template, resets per-session state, creates
        the frame extractor config, and starts frame capture.

        Args:
            source_url: Video source URL to capture frames from.
            skip_frames: Process every Nth frame from the stream.
        """
        if self._state.status == PipelineStatus.RUNNING:
            logger.warning("Pipeline is already running, ignoring start request.")
            return

        # Resolve the active ROI template
        template_result = self._template_registry.get(self._state.active_template_name)
        if isinstance(template_result, TemplateNotFoundError):
            # Fallback to default
            template_result = self._template_registry.get_default()
            if isinstance(template_result, TemplateNotFoundError):
                logger.error(
                    f"No ROI template available: {template_result.template_name}. "
                    f"Available: {template_result.available_templates}"
                )
                await self._broadcast({
                    "type": "error",
                    "payload": {
                        "message": "No ROI template loaded. Cannot start pipeline.",
                    },
                })
                return

        self._roi_config = template_result

        # Reset per-session state
        self._stage_assigner.reset()
        self._record_assembler.reset()
        self._state.source_url = source_url
        self._state.skip_frames = skip_frames
        self._state.current_sequence = 0
        self._state.separation_state = SeparationState.PRE_SEPARATION

        # Ensure OCR engine is loaded
        self._ensure_ocr_engine()

        # Create frame extractor config and start
        config = FrameExtractorConfig(
            source_url=source_url,
            skip_frames=skip_frames,
        )

        self._frame_extractor.start(config)
        self._state.status = PipelineStatus.RUNNING

        # Broadcast status change
        await self._broadcast_status()

    def stop(self) -> None:
        """Stop the extraction pipeline.

        Stops frame capture and resets per-session components.
        """
        if self._state.status == PipelineStatus.STOPPED:
            logger.info("Pipeline is already stopped.")
            return

        self._frame_extractor.stop()
        self._stage_assigner.reset()
        self._record_assembler.reset()

        self._state.status = PipelineStatus.STOPPED
        self._state.current_sequence = 0
        self._state.separation_state = SeparationState.PRE_SEPARATION

    def set_skip_frames(self, skip_frames: int) -> None:
        """Update the frame skip count.

        Updates both the pipeline state and the frame extractor's active config.
        Takes effect on the next frame read cycle.

        Args:
            skip_frames: Process every Nth frame (minimum 1).
        """
        skip_frames = max(1, min(skip_frames, 300))
        self._state.skip_frames = skip_frames
        if self._frame_extractor._config is not None:
            self._frame_extractor._config.skip_frames = skip_frames
        logger.info(f"Frame skip set to every {skip_frames} frames")

    async def _on_frame(self, frame: np.ndarray, seq: int) -> None:
        """Process a single captured frame through the full pipeline.

        Pipeline stages:
        1. Engine Analysis (OpenCV Hough Circle Detection)
        2. OCR Extraction (EasyOCR)
        3. Stage Assignment
        4. Record Assembly
        5. Broadcast to WebSocket clients

        Errors on a single frame are logged and skipped rather than
        crashing the pipeline.

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
        """
        if self._roi_config is None:
            logger.warning("No ROI configuration available, skipping frame.")
            return

        frame_start_time = time.perf_counter()

        try:
            # Step 1: Engine Analysis
            engine_result = analyze_engines(
                frame=frame,
                engine_groups=self._roi_config.engine_groups,
                config=self._engine_analyzer_config,
                gpu_capabilities=self._gpu_capabilities,
            )

            # Step 2: OCR Extraction
            ocr_engine = self._ensure_ocr_engine()

            # After stage separation is detected, skip the stage_sep_text ROI
            text_regions = self._roi_config.text_regions
            if self._state.separation_state == SeparationState.POST_SEPARATION:
                text_regions = {
                    k: v for k, v in text_regions.items() if k != "stage_sep_text"
                }

            ocr_result = ocr_engine.extract_text(
                frame=frame,
                text_regions=text_regions,
            )

            # Step 3: Stage Assignment
            stage_result = self._stage_assigner.assign(ocr_result)

            # Step 4: Record Assembly
            record = self._record_assembler.assemble(
                engine_result=engine_result,
                ocr_result=ocr_result,
                stage_result=stage_result,
                engine_groups=self._roi_config.engine_groups,
                t_zero_found=ocr_engine.t_zero_detected,
                stage_sep_found=stage_result.separation_state == SeparationState.POST_SEPARATION,
            )

            # Update state
            self._state.current_sequence = record.sequence_number
            self._state.separation_state = stage_result.separation_state

            # Compute FPS measurement
            frame_duration = time.perf_counter() - frame_start_time
            self._fps_samples.append(frame_duration)
            if len(self._fps_samples) > 10:
                self._fps_samples = self._fps_samples[-10:]
            avg_duration = sum(self._fps_samples) / len(self._fps_samples)
            self._state.processing_fps = round(1.0 / avg_duration, 2) if avg_duration > 0 else 0.0

            # Step 5: Broadcast telemetry record
            await self._broadcast({
                "type": "telemetry",
                "payload": record.model_dump(),
            })

            # Step 6: Broadcast current frame as JPEG for live preview with FPS
            try:
                _, jpeg_buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                frame_b64 = base64.b64encode(jpeg_buf.tobytes()).decode('ascii')
                await self._broadcast({
                    "type": "frame",
                    "payload": {
                        "image": frame_b64,
                        "sequence": record.sequence_number,
                        "processing_fps": self._state.processing_fps,
                    },
                })
            except Exception as frame_err:
                logger.warning(f"Failed to encode/broadcast frame: {frame_err}")

        except Exception as e:
            logger.error(f"Error processing frame {seq}: {e}", exc_info=True)
            # Skip this frame, pipeline continues

    async def _on_status_change(self, new_status: PipelineStatus) -> None:
        """Handle frame extractor status changes.

        Updates internal state and broadcasts the new status to clients.

        Args:
            new_status: The new pipeline status from the frame extractor.
        """
        self._state.status = new_status
        await self._broadcast_status()

    async def _broadcast_status(self) -> None:
        """Broadcast the current pipeline status to all connected clients."""
        await self._broadcast({
            "type": "status",
            "payload": self.build_status_payload(),
        })

    def build_status_payload(self) -> dict[str, Any]:
        """Build the pipeline status payload dictionary.

        Returns:
            Dictionary with status, gpu info, skip_frames,
            current_sequence, and processing_fps for API and WebSocket responses.
        """
        return {
            "status": self._state.status.value,
            "gpu": {
                "available": self._gpu_capabilities.gpu_available,
                "device_name": self._gpu_capabilities.device_name,
            },
            "skip_frames": self._state.skip_frames,
            "current_sequence": self._state.current_sequence,
            "processing_fps": self._state.processing_fps,
        }
