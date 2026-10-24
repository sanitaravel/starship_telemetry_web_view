"""The merged frame path: FrameProcessor.process_frame and its stage-failure fallbacks.

The heavy stages (OCR, engine analysis, JPEG encoding) are stubbed so the
tests exercise only the orchestration logic and run fast.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.core.enums import EngineStatus, OCRFieldStatus
from src.core.gpu import AccelerationBackend, GPUCapabilities
from src.ocr.results import OCRFieldResult, OCRResult
from src.pipeline import PipelineOrchestrator
from src.pipeline.reorder_buffer import ReorderBuffer
from src.pipeline.stages import unpack_stage_results
from src.templates.template_registry import TemplateRegistry, load_default_template
from src.vision.engine_analyzer import EngineAnalysisResult

_T_ZERO_LOG = "T-0 detected during sequential processing"

_FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)


def _engine_result() -> EngineAnalysisResult:
    return EngineAnalysisResult(
        engine_statuses={"e0": EngineStatus.ACTIVE},
        detection_accuracy=1.0,
        engine_group_bounding_boxes=[],
    )


def _ocr_result() -> OCRResult:
    return OCRResult.unavailable(
        time=OCRFieldResult(
            status=OCRFieldStatus.AVAILABLE, raw_text="T+00:00:01", parsed_value="00:00:01"
        )
    )


@pytest.fixture
def orchestrator() -> PipelineOrchestrator:
    """An orchestrator wired just enough for process_frame, with stubbed stages."""
    registry = TemplateRegistry()
    load_default_template(registry)
    cpu = GPUCapabilities(backend=AccelerationBackend.CPU, device_name=None,
                          cuda_version=None, gpu_available=False)
    orch = PipelineOrchestrator(cpu, registry, broadcast=AsyncMock())
    orch._frames.roi_config = registry.get_default()
    orch._ordering.buffer = ReorderBuffer()
    orch._broadcaster.queue = asyncio.Queue()
    # A stand-in OCR engine: ensure_ocr_engine returns it without loading EasyOCR
    orch._frames.ocr_engine = SimpleNamespace(t_zero_detected=False)
    orch._frames.run_engine_analysis = AsyncMock(side_effect=lambda frame: _engine_result())
    orch._frames.run_ocr_extraction = AsyncMock(side_effect=lambda frame, *regions: _ocr_result())
    orch._frames.run_jpeg_encoding = AsyncMock(return_value="anBlZw==")
    return orch


def _telemetry_messages(orch: PipelineOrchestrator) -> list[dict]:
    sent = []
    while not orch._broadcaster.queue.empty():
        message = orch._broadcaster.queue.get_nowait()
        if message["type"] == "telemetry":
            sent.append(message)
    return sent


def _t_zero_logs(src_caplog) -> list[logging.LogRecord]:
    return [r for r in src_caplog.records
            if r.levelno == logging.INFO and _T_ZERO_LOG in r.getMessage()]


# --- unpack_stage_results ---


class TestUnpackStageResults:
    def test_both_succeed_passes_results_through(self, orchestrator):
        engine, ocr = _engine_result(), _ocr_result()
        assert unpack_stage_results(1, engine, ocr) == (engine, ocr)

    def test_engine_exception_falls_back_to_empty(self, orchestrator, src_caplog):
        ocr = _ocr_result()
        with src_caplog.at_level(logging.ERROR, logger="src.pipeline"):
            unpacked = unpack_stage_results(7, RuntimeError("boom"), ocr)
        assert unpacked is not None
        engine_result, ocr_result = unpacked
        assert engine_result == EngineAnalysisResult.empty()
        assert ocr_result is ocr
        assert any("Engine analysis failed for frame 7" in r.getMessage()
                   for r in src_caplog.records)

    def test_ocr_exception_falls_back_to_all_unavailable(self, orchestrator, src_caplog):
        engine = _engine_result()
        with src_caplog.at_level(logging.ERROR, logger="src.pipeline"):
            unpacked = unpack_stage_results(3, engine, ValueError("bad"))
        assert unpacked is not None
        engine_result, ocr_result = unpacked
        assert engine_result is engine
        assert ocr_result == OCRResult.unavailable()
        for name in OCRResult.__dataclass_fields__:
            assert getattr(ocr_result, name).status == OCRFieldStatus.UNAVAILABLE, name
        assert any("OCR extraction failed for frame 3" in r.getMessage()
                   for r in src_caplog.records)

    def test_both_exceptions_return_none(self, orchestrator, src_caplog):
        with src_caplog.at_level(logging.ERROR, logger="src.pipeline"):
            unpacked = unpack_stage_results(
                9, RuntimeError("engine"), RuntimeError("ocr")
            )
        assert unpacked is None
        assert any("Both engine analysis and OCR failed for frame 9" in r.getMessage()
                   for r in src_caplog.records)

    def test_base_exception_subclass_is_treated_as_failure(self, orchestrator):
        """gather(return_exceptions=True) can hand back CancelledError (a BaseException)."""
        unpacked = unpack_stage_results(
            1, asyncio.CancelledError(), _ocr_result()
        )
        assert unpacked is not None
        assert unpacked[0] == EngineAnalysisResult.empty()


# --- process_frame ---


class TestProcessFrameTZero:
    async def test_detect_t_zero_flips_state_once(self, orchestrator, src_caplog):
        orchestrator._frames.ocr_engine.t_zero_detected = True
        with src_caplog.at_level(logging.INFO, logger="src.pipeline"):
            await orchestrator._frames.process_frame(_FRAME, 1, 30, 30.0, detect_t_zero=True)

            assert orchestrator._frames.t_zero_detected is True
            assert orchestrator.state.t_zero_detected is True
            assert orchestrator.state.parallel_mode_active is True
            assert len(_t_zero_logs(src_caplog)) == 1

            # Later frames with the flag still set do not re-log
            await orchestrator._frames.process_frame(_FRAME, 2, 60, 30.0, detect_t_zero=True)
            await orchestrator._frames.process_frame(_FRAME, 3, 90, 30.0, detect_t_zero=True)

        assert len(_t_zero_logs(src_caplog)) == 1
        assert orchestrator._frames.t_zero_detected is True
        assert orchestrator.state.parallel_mode_active is True
        assert [m["payload"]["sequence_number"] for m in _telemetry_messages(orchestrator)] \
            == [1, 2, 3]

    async def test_no_flip_before_ocr_reports_t_zero(self, orchestrator, src_caplog):
        orchestrator._frames.ocr_engine.t_zero_detected = False
        with src_caplog.at_level(logging.INFO, logger="src.pipeline"):
            await orchestrator._frames.process_frame(_FRAME, 1, 30, 30.0, detect_t_zero=True)
        assert orchestrator._frames.t_zero_detected is False
        assert orchestrator.state.t_zero_detected is False
        assert orchestrator.state.parallel_mode_active is False
        assert _t_zero_logs(src_caplog) == []

    async def test_detect_t_zero_false_never_flips(self, orchestrator, src_caplog):
        orchestrator._frames.ocr_engine.t_zero_detected = True
        with src_caplog.at_level(logging.INFO, logger="src.pipeline"):
            await orchestrator._frames.process_frame(_FRAME, 1, 30, 30.0, detect_t_zero=False)
        assert orchestrator._frames.t_zero_detected is False
        assert orchestrator.state.t_zero_detected is False
        assert orchestrator.state.parallel_mode_active is False
        assert _t_zero_logs(src_caplog) == []
        # The frame itself is still processed and broadcast
        assert len(_telemetry_messages(orchestrator)) == 1

    async def test_stages_run_once_per_frame(self, orchestrator):
        await orchestrator._frames.process_frame(_FRAME, 1, 30, 30.0, detect_t_zero=True)
        orchestrator._frames.run_engine_analysis.assert_awaited_once()
        orchestrator._frames.run_ocr_extraction.assert_awaited_once()
        orchestrator._frames.run_jpeg_encoding.assert_awaited_once()


class TestProcessFrameFallbacks:
    async def test_one_stage_failing_still_broadcasts(self, orchestrator):
        orchestrator._frames.run_engine_analysis = AsyncMock(side_effect=RuntimeError("engine"))
        await orchestrator._frames.process_frame(_FRAME, 1, 30, 30.0, detect_t_zero=False)
        sent = _telemetry_messages(orchestrator)
        assert len(sent) == 1
        assert sent[0]["payload"]["sequence_number"] == 1

    async def test_both_stages_failing_skips_frame(self, orchestrator):
        orchestrator._frames.run_engine_analysis = AsyncMock(side_effect=RuntimeError("engine"))
        orchestrator._frames.run_ocr_extraction = AsyncMock(side_effect=RuntimeError("ocr"))
        await orchestrator._frames.process_frame(_FRAME, 1, 30, 30.0, detect_t_zero=True)
        assert _telemetry_messages(orchestrator) == []
        # The skipped seq no longer holds up the buffer
        assert orchestrator._ordering.buffer.next_expected == 2
        assert orchestrator._frames.t_zero_detected is False

    async def test_jpeg_failure_keeps_telemetry(self, orchestrator):
        orchestrator._frames.run_jpeg_encoding = AsyncMock(side_effect=RuntimeError("jpeg"))
        await orchestrator._frames.process_frame(_FRAME, 1, 30, 30.0, detect_t_zero=False)
        assert len(_telemetry_messages(orchestrator)) == 1

    async def test_with_timeout_wrapper_does_not_detect_t_zero(self, orchestrator):
        orchestrator._frames.ocr_engine.t_zero_detected = True
        await orchestrator._frames.process_frame_with_timeout(_FRAME, 1, 30, 30.0)
        assert orchestrator._frames.t_zero_detected is False
        assert orchestrator.state.parallel_mode_active is False
        assert len(_telemetry_messages(orchestrator)) == 1
