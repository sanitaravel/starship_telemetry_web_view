"""Per-frame work: engine analysis, OCR and preview encoding, then record assembly.

The CPU-bound stages run concurrently in thread pools with timeouts. The
assembled record goes to the reorder coordinator, which broadcasts results
in video order.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from src.core.enums import SeparationState
from src.core.logging_context import frame_seq_var
from src.core.models import ROIConfiguration
from src.ocr.engine import EasyOCREngine
from src.ocr.results import OCRResult
from src.pipeline.ordering import ReorderCoordinator
from src.pipeline.parallel_config import ParallelPipelineConfig
from src.pipeline.stages import encode_preview, run_timed, unpack_stage_results
from src.pipeline.state import PipelineState
from src.telemetry.record_assembler import RecordAssembler
from src.vision.engine_analyzer import EngineAnalyzerConfig, EngineAnalysisResult, analyze_engines
from src.vision.gpu_detector import GPUCapabilities


logger = logging.getLogger(__name__)


class FrameProcessor:
    """Processes single frames and owns the resources that work needs.

    Holds the OCR engine (loaded lazily), the active ROI configuration, the
    thread pools and the T-0 flag that switches the orchestrator from
    sequential to parallel frame processing.
    """

    def __init__(
        self,
        gpu_capabilities: GPUCapabilities,
        parallel_config: ParallelPipelineConfig,
        state: PipelineState,
        record_assembler: RecordAssembler,
        ordering: ReorderCoordinator,
    ) -> None:
        """Initialize the frame processor.

        Args:
            gpu_capabilities: Detected GPU capabilities for OCR acceleration.
            parallel_config: Timeouts and thread pool sizing.
            state: Pipeline state (separation state, T-0 and parallel flags).
            record_assembler: Assembles each frame's TelemetryRecord.
            ordering: Reorder coordinator that results are submitted to.
        """
        self._gpu_capabilities = gpu_capabilities
        self._parallel_config = parallel_config
        self._state = state
        self._record_assembler = record_assembler
        self._ordering = ordering

        self.ocr_engine: EasyOCREngine | None = None  # Lazy init (expensive)
        self.engine_analyzer_config = EngineAnalyzerConfig()

        # Active ROI configuration (resolved from template on start)
        self.roi_config: ROIConfiguration | None = None

        # Thread pools for CPU-bound work: OCR gets its own pool sized to the
        # hardware so engine analysis and JPEG encoding never queue behind it
        self.executor: ThreadPoolExecutor | None = None
        self.ocr_executor: ThreadPoolExecutor | None = None

        self.t_zero_detected: bool = False

    def ensure_ocr_engine(self) -> EasyOCREngine:
        """Lazily initialize the OCR engine (expensive neural net load).

        Returns:
            The initialized EasyOCREngine instance.
        """
        if self.ocr_engine is None:
            logger.info("Initializing EasyOCR engine (this may take a moment)...")
            self.ocr_engine = EasyOCREngine(self._gpu_capabilities)
            logger.info("EasyOCR engine initialized.")
        return self.ocr_engine

    def open_pools(self) -> int:
        """Create the thread pools for CPU-bound work.

        Returns:
            The number of OCR workers.
        """
        self.executor = ThreadPoolExecutor(
            max_workers=self._parallel_config.executor_max_workers,
            thread_name_prefix="pipeline",
        )
        ocr_workers = self._parallel_config.resolve_ocr_workers(
            self._gpu_capabilities.gpu_available
        )
        self.ocr_executor = ThreadPoolExecutor(
            max_workers=ocr_workers, thread_name_prefix="ocr"
        )
        logger.info("OCR thread pool: %d workers.", ocr_workers)
        return ocr_workers

    async def close_pools(self) -> None:
        """Shut down the thread pools with a timeout each (Req 2.4)."""
        for executor in (self.executor, self.ocr_executor):
            if executor is not None:
                await self._shutdown_executor(executor)
        self.executor = None
        self.ocr_executor = None

    async def _shutdown_executor(self, executor: ThreadPoolExecutor) -> None:
        """Shut down a thread pool, forcing release after shutdown_timeout_seconds."""
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: executor.shutdown(wait=True, cancel_futures=True),
                ),
                timeout=self._parallel_config.shutdown_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Thread pool executor did not shut down within %.1f seconds. "
                "Forcing release.",
                self._parallel_config.shutdown_timeout_seconds,
            )
            # Force a non-blocking shutdown
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error shutting down executor: {e}")

    async def run_engine_analysis(self, frame: np.ndarray) -> EngineAnalysisResult:
        """Run engine analysis in the thread pool executor.

        Offloads the CPU-bound OpenCV Hough Circle Detection to the thread
        pool, with a configurable timeout to prevent stalls.

        Args:
            frame: BGR numpy array (1920x1080).

        Returns:
            EngineAnalysisResult with engine statuses and detection accuracy.
            Returns a default result with empty statuses on timeout.
        """
        try:
            result, duration_ms, queue_ms = await asyncio.wait_for(
                run_timed(
                    self.executor,
                    analyze_engines,
                    frame,
                    self.roi_config.engine_groups,
                    self.engine_analyzer_config,
                    self._gpu_capabilities,
                ),
                timeout=self._parallel_config.stage_timeout_seconds,
            )
            seq = frame_seq_var.get()
            logger.debug(
                "Engine analysis completed",
                extra={"frame_seq": seq, "duration_ms": duration_ms, "queue_ms": queue_ms},
            )
            return result
        except asyncio.TimeoutError:
            seq = frame_seq_var.get()
            logger.warning(
                "Engine analysis timed out after %.1f seconds"
                " (stage=engine_analysis, frame_seq=%s, stage_timeout_seconds=%.1f)",
                self._parallel_config.stage_timeout_seconds,
                seq,
                self._parallel_config.stage_timeout_seconds,
            )
            return EngineAnalysisResult.empty()

    async def run_ocr_extraction(self, frame: np.ndarray) -> OCRResult:
        """Run OCR text extraction in the thread pool executor.

        Offloads the CPU-bound EasyOCR/PyTorch inference to the thread
        pool, with a configurable timeout to prevent stalls.

        Args:
            frame: BGR numpy array (1920x1080).

        Returns:
            OCRResult with extracted text fields.
            Returns a default result with all fields UNAVAILABLE on timeout.
        """
        ocr_engine = self.ensure_ocr_engine()

        # After stage separation, skip the stage_sep_text ROI
        text_regions = self.roi_config.text_regions
        if self._state.separation_state == SeparationState.POST_SEPARATION:
            text_regions = {
                k: v for k, v in text_regions.items() if k != "stage_sep_text"
            }

        try:
            result, duration_ms, queue_ms = await asyncio.wait_for(
                run_timed(
                    self.ocr_executor,
                    ocr_engine.extract_text,
                    frame,
                    text_regions,
                ),
                timeout=self._parallel_config.stage_timeout_seconds,
            )
            seq = frame_seq_var.get()
            logger.debug(
                "OCR extraction completed",
                extra={"frame_seq": seq, "duration_ms": duration_ms, "queue_ms": queue_ms},
            )
            return result
        except asyncio.TimeoutError:
            seq = frame_seq_var.get()
            logger.warning(
                "OCR extraction timed out after %.1f seconds"
                " (stage=ocr_extraction, frame_seq=%s, stage_timeout_seconds=%.1f)",
                self._parallel_config.stage_timeout_seconds,
                seq,
                self._parallel_config.stage_timeout_seconds,
            )
            return OCRResult.unavailable()

    async def run_jpeg_encoding(self, frame: np.ndarray) -> str:
        """Encode a downscaled preview of the frame as JPEG in the thread pool.

        Offloads the CPU-bound resize, JPEG encoding and base64 conversion to
        the thread pool. No timeout is applied for JPEG encoding.

        Args:
            frame: BGR numpy array (1920x1080).

        Returns:
            Base64-encoded JPEG string.
        """
        result, duration_ms, queue_ms = await run_timed(self.executor, encode_preview, frame)
        logger.debug(
            "Preview encoding completed",
            extra={"duration_ms": duration_ms, "queue_ms": queue_ms},
        )
        return result

    async def process_frame_with_timeout(
        self, frame: np.ndarray, seq: int, frame_number: int, source_fps: float
    ) -> None:
        """Wrap frame processing with a configurable timeout.

        Calls process_frame within asyncio.wait_for using
        frame_timeout_seconds (default 10s). On timeout, logs a warning,
        advances the reorder buffer's expected sequence past the timed-out
        frame, and drains any consecutive results that become available.

        The concurrency slot release and task tracking cleanup happen in the
        orchestrator's _on_task_done callback regardless of whether this
        method completes normally or via timeout.

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
            frame_number: Absolute source frame index in the original stream.
            source_fps: Source video frame rate in frames per second.
        """
        try:
            await asyncio.wait_for(
                self.process_frame(
                    frame, seq, frame_number, source_fps, detect_t_zero=False
                ),
                timeout=self._parallel_config.frame_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Frame %d timed out after %.1f seconds (frame-level timeout). "
                "Advancing past frame. (frame_seq=%d, frame_timeout_seconds=%.1f)",
                seq,
                self._parallel_config.frame_timeout_seconds,
                seq,
                self._parallel_config.frame_timeout_seconds,
            )
            self._ordering.skip_frame(seq)
            await self._ordering.drain()

    async def process_frame(
        self,
        frame: np.ndarray,
        seq: int,
        frame_number: int,
        source_fps: float,
        *,
        detect_t_zero: bool,
    ) -> None:
        """Process a frame with intra-frame parallelism using asyncio.gather.

        Runs engine analysis, OCR extraction and JPEG encoding concurrently
        in the thread pools. If one stage fails, uses a default result for
        the failed stage and continues with the successful result. If both
        fail, the frame is skipped.

        Used both pre-T-0 (awaited inline from the orchestrator's _on_frame,
        so frames are processed one at a time in capture order) and post-T-0
        (spawned as a task via process_frame_with_timeout, overlapping other
        frames).

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
            frame_number: Absolute source frame index in the original stream.
            source_fps: Source video frame rate in frames per second.
            detect_t_zero: When True (pre-T-0 sequential mode), checks after
                OCR whether T-0 was detected and, if so, enables parallel
                mode for subsequent frames.
        """
        if self.roi_config is None:
            logger.warning("No ROI configuration available, skipping frame.")
            return

        frame_start = time.monotonic()
        try:
            # Step 1: Run engine analysis and OCR extraction concurrently
            # (intra-frame parallelism), plus JPEG encoding
            engine_result_or_exc, ocr_result_or_exc, frame_b64 = await asyncio.gather(
                self.run_engine_analysis(frame),
                self.run_ocr_extraction(frame),
                self.run_jpeg_encoding(frame),
                return_exceptions=True,
            )

            # Step 2: Fall back for a failed stage; skip the frame if both failed
            unpacked = unpack_stage_results(seq, engine_result_or_exc, ocr_result_or_exc)
            if unpacked is None:
                self._ordering.skip_frame(seq)
                await self._ordering.drain()
                return
            engine_result, ocr_result = unpacked

            # Step 3: Pre-T-0 only, check for T-0 detection after OCR completes
            ocr_engine = self.ensure_ocr_engine()
            if detect_t_zero and not self.t_zero_detected and ocr_engine.t_zero_detected:
                self.t_zero_detected = True
                self._state.t_zero_detected = True
                self._state.parallel_mode_active = True
                logger.info(
                    "T-0 detected during sequential processing of frame %d, "
                    "enabling parallel frame processing mode.",
                    seq,
                )

            # Step 4: Stage assignment runs when the result leaves the reorder
            # buffer (see ReorderCoordinator.drain), in video order; the record
            # gets a placeholder until then
            stage_result = self._ordering.pending_stage_result()

            # Step 5: Record Assembly
            record = self._record_assembler.assemble(
                engine_result=engine_result,
                ocr_result=ocr_result,
                stage_result=stage_result,
                engine_groups=self.roi_config.engine_groups,
                frame_number=frame_number,
                source_fps=source_fps,
                t_zero_found=ocr_engine.t_zero_detected,
                stage_sep_found=stage_result.separation_state == SeparationState.POST_SEPARATION,
            )

            # Step 6: Handle frame_b64 exception (only pass str | None to buffer)
            actual_frame_b64: str | None = None
            if isinstance(frame_b64, BaseException):
                logger.warning(
                    "Failed to encode frame %d: %s", seq, frame_b64
                )
            else:
                actual_frame_b64 = frame_b64

            # Step 7: Submit to reorder buffer for ordered broadcast
            await self._ordering.submit(seq, record, actual_frame_b64, ocr_result)

            # Log total frame processing duration
            frame_end = time.monotonic()
            total_duration_ms = int((frame_end - frame_start) * 1000)
            logger.debug(
                "Frame processing completed",
                extra={"frame_seq": seq, "duration_ms": total_duration_ms},
            )

        except Exception as e:
            logger.error(f"Error processing frame {seq}: {e}", exc_info=True)
            # Skip this frame, pipeline continues
            self._ordering.skip_frame(seq)
            await self._ordering.drain()
