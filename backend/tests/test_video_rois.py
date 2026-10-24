"""The optional video ROIs: HUD milestone labels, the countdown clocks, and their plumbing.

Covers the template (``timeline_below``, ``time_sign``, ``countdown_time``),
parsing of signed clocks, which ROIs each frame reads (``RegionSchedule``),
the OCR engine's countdown reads before T-0, a real read of the pre-launch
clock (when the EasyOCR weights are installed), and the orchestrator feeding
HUD labels and countdown ticks to the timeline.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np
import pytest

from src.core.enums import EngineStatus, OCRFieldStatus, SeparationState
from src.core.gpu import AccelerationBackend, GPUCapabilities
from src.core.models import ParseError, ROIRect
from src.ocr.engine import EasyOCREngine
from src.ocr.parsing import get_allowlist, parse_field_value, parse_sign_value, parse_signed_time_value
from src.ocr.results import OCRFieldResult, OCRResult
from src.pipeline import PipelineOrchestrator
from src.pipeline.region_schedule import RegionSchedule
from src.pipeline.reorder_buffer import ReorderBuffer
from src.templates.svg_parser import parse_roi_template
from src.templates.template_registry import TemplateRegistry, load_default_template
from src.vision.engine_analyzer import EngineAnalysisResult

CPU = GPUCapabilities(backend=AccelerationBackend.CPU, device_name=None, cuda_version=None, gpu_available=False)
FIXTURES = Path(__file__).parent / "fixtures"


def default_config():
    registry = TemplateRegistry()
    assert load_default_template(registry) is None
    return registry.get_default()


def available(text, parsed=None):
    return OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text=text, parsed_value=parsed or text)


# --- Template ---


def test_default_template_has_the_optional_rois():
    regions = default_config().text_regions
    assert regions["timeline_below"] == ROIRect("timeline_below", 914.5, 936.5, 85, 21)
    assert regions["time_sign"] == ROIRect("time_sign", 830, 965, 36, 47)
    assert regions["countdown_time"] == ROIRect("countdown_time", 30, 64, 202, 46)
    # the label spot above the line is unchanged (stage assignment reads it)
    assert regions["stage_sep_text"] == ROIRect("stage_sep_text", 914.5, 904.5, 85, 15)


def _svg(extra: str = "") -> str:
    rect = 'x="0" y="0" width="10" height="10"'
    groups = "".join(
        f'<g id="{g}"><rect id="value" {rect}/><rect id="unit" {rect}/></g>'
        for g in ("altitude_r", "speed_r", "altitude_l", "speed_l")
    )
    engines = "".join(
        f'<g id="{g}"><g id="a"><circle id="{g}_1" cx="5" cy="5" r="2"/></g></g>'
        for g in ("engines_starship", "engines_superheavy")
    )
    tops = "".join(f'<rect id="{r}" {rect}/>' for r in ("time", "stage_r", "stage_l", "stage_sep_text"))
    return (
        '<svg width="1920" height="1080" viewBox="0 0 1920 1080" xmlns="http://www.w3.org/2000/svg">'
        f'<g id="f">{tops}{groups}{engines}{extra}</g></svg>'
    )


def test_template_without_optional_rois_still_parses():
    config = parse_roi_template(_svg())
    assert not isinstance(config, ParseError)
    assert not {"timeline_below", "time_sign", "countdown_time"} & set(config.text_regions)


def test_malformed_optional_roi_is_an_error():
    result = parse_roi_template(_svg('<rect id="time_sign" x="1" width="5"/>'))
    assert isinstance(result, ParseError)
    assert "time_sign" in result.malformed_elements


# --- Parsing ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("T- 00:12:31", "-00:12:31"),
        ("T-00:00:27", "-00:00:27"),
        ("T+00:00:05", "+00:00:05"),
        ("00:12:31", "-00:12:31"),  # sign not recognised: the countdown counts down
        ("T- 0:12:31", "-00:12:31"),
        ("T- 00:61:00", None),
        ("STARSHIP", None),
    ],
)
def test_parse_signed_time(text, expected):
    assert parse_signed_time_value(text) == expected
    assert parse_field_value("countdown_time", text) == expected


@pytest.mark.parametrize("text, expected", [("T-", "-"), ("T+", "+"), ("-", "-"), ("T", None), ("T+-", None)])
def test_parse_sign(text, expected):
    assert parse_sign_value(text) == expected
    assert parse_field_value("time_sign", text) == expected


def test_countdown_rois_share_one_allowlist_and_labels_keep_the_label_one():
    assert get_allowlist("countdown_time") == get_allowlist("time_sign")
    assert set("T+-:0123456789") <= set(get_allowlist("countdown_time"))
    assert get_allowlist("timeline_below") is get_allowlist("stage_sep_text") is None


# --- RegionSchedule ---


REGIONS = default_config().text_regions


def test_before_t_zero_countdown_rois_are_read_once_a_second():
    schedule = RegionSchedule(hud_interval_s=2.0, countdown_interval_s=1.0)
    picks = [schedule.regions(REGIONS, t, t_zero=False, post_separation=False) for t in (0.0, 0.5, 1.0, 1.9, 2.0)]
    assert [due for _, due in picks] == [True, False, True, False, True]
    regions, _ = picks[0]
    assert {"countdown_time", "time_sign", "time"} <= set(regions)
    assert "timeline_below" not in regions
    assert "countdown_time" not in picks[1][0]


def test_after_t_zero_hud_spots_follow_the_hud_rate():
    schedule = RegionSchedule(hud_interval_s=2.0, countdown_interval_s=1.0)
    pre = [schedule.regions(REGIONS, t, t_zero=True, post_separation=False)[0] for t in (30.0, 31.0, 32.0)]
    # timeline_below at the HUD rate; stage_sep_text on every frame before separation
    assert ["timeline_below" in r for r in pre] == [True, False, True]
    assert all("stage_sep_text" in r for r in pre)
    assert not any("countdown_time" in r or "time_sign" in r for r in pre)
    post = [schedule.regions(REGIONS, t, t_zero=True, post_separation=True)[0] for t in (33.0, 34.0, 35.0)]
    # after separation stage_sep_text is no longer dropped, only read at the HUD rate
    assert ["stage_sep_text" in r for r in post] == [False, True, False]
    assert ["timeline_below" in r for r in post] == [False, True, False]


def test_unknown_video_time_reads_every_frame():
    schedule = RegionSchedule(hud_interval_s=2.0, countdown_interval_s=1.0)
    for _ in range(3):
        regions, _ = schedule.regions(REGIONS, None, t_zero=True, post_separation=True)
        assert "timeline_below" in regions and "stage_sep_text" in regions


def test_template_without_optional_rois_reads_as_before():
    schedule = RegionSchedule(hud_interval_s=2.0, countdown_interval_s=1.0)
    plain = {k: v for k, v in REGIONS.items() if k not in ("timeline_below", "time_sign", "countdown_time")}
    regions, due = schedule.regions(plain, 0.0, t_zero=False, post_separation=False)
    assert regions == plain and not due


# --- OCR engine before T-0 ---


class ScriptedBatcher:
    """Answers each box with the text scripted for the ROI at that position."""

    def __init__(self, texts: dict[tuple[int, int], str]):
        self.texts = texts
        self.calls: list[list[list[int]]] = []

    def recognize(self, image, items):
        self.calls.append([box for box, _ in items])
        out = []
        for (x1, _x2, y1, _y2), _allow in items:
            text = self.texts.get((x1, y1))
            out.append(None if text is None else ([[x1, y1]], text, 0.95))
        return out


def scripted_engine(texts):
    with patch("src.ocr.engine.easyocr.Reader"):
        engine = EasyOCREngine(CPU, use_detection=False)
    engine._batcher = ScriptedBatcher(texts)
    return engine


FRAME = np.full((1080, 1920, 3), 128, dtype=np.uint8)
TIME_AT, SIGN_AT, TOP_AT = (868, 965), (830, 965), (30, 64)
COUNTDOWN_REGIONS = {k: REGIONS[k] for k in ("time", "time_sign", "countdown_time", "speed_l")}


def test_countdown_rois_are_read_with_the_gate_and_kept_apart_from_telemetry():
    engine = scripted_engine({TIME_AT: "00:00:27", SIGN_AT: "T-", TOP_AT: "T- 00:00:30"})

    result = engine.extract_text(FRAME, COUNTDOWN_REGIONS)

    assert len(engine._batcher.calls) == 1 and len(engine._batcher.calls[0]) == 3  # one request
    assert result.time.status == OCRFieldStatus.UNAVAILABLE  # no record gets a MET
    assert result.countdown_clock.parsed_value == "00:00:27"
    assert result.time_sign.parsed_value == "-"
    assert result.countdown_time.parsed_value == "-00:00:30"
    assert not engine.t_zero_detected


def test_hud_clock_at_t_minus_zero_still_detects_t_zero_as_before():
    engine = scripted_engine({TIME_AT: "00:00:00", SIGN_AT: "T-", (99, 968): "1234"})

    result = engine.extract_text(FRAME, COUNTDOWN_REGIONS)

    assert engine.t_zero_detected
    assert result.time.parsed_value == "T+00:00:00"
    assert result.countdown_time.status == OCRFieldStatus.UNAVAILABLE  # not read on the T-0 frame


def test_unreadable_countdown_clock_is_unavailable():
    engine = scripted_engine({TIME_AT: "12", TOP_AT: "STARSHIP FLIGHT"})
    result = engine.extract_text(FRAME, COUNTDOWN_REGIONS)
    assert result.countdown_time.status == OCRFieldStatus.UNAVAILABLE
    assert result.countdown_clock.status == OCRFieldStatus.UNAVAILABLE


@pytest.fixture(scope="module")
def real_engine():
    weights = Path.home() / ".EasyOCR" / "model" / "english_g2.pth"
    if not weights.exists():
        pytest.skip("EasyOCR model weights not installed")
    return EasyOCREngine(CPU, use_detection=False)


def test_real_read_of_the_prelaunch_clock(real_engine):
    """The top-left 330x170 px of a real 1920x1080 frame showing T- 00:12:31."""
    crop = cv2.imread(str(FIXTURES / "prelaunch_clock_T-001231_topleft.png"))
    assert crop is not None
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame[: crop.shape[0], : crop.shape[1]] = crop
    real_engine.reset()

    result = real_engine.extract_text(frame, COUNTDOWN_REGIONS)

    assert result.countdown_time.parsed_value == "-00:12:31"
    assert result.time.status == OCRFieldStatus.UNAVAILABLE
    assert not real_engine.t_zero_detected


# --- Orchestrator plumbing ---


def _engine_result():
    return EngineAnalysisResult(
        engine_statuses={"e0": EngineStatus.ACTIVE}, detection_accuracy=1.0, engine_group_bounding_boxes=[]
    )


@pytest.fixture
def orchestrator():
    registry = TemplateRegistry()
    load_default_template(registry)
    orch = PipelineOrchestrator(CPU, registry, broadcast=AsyncMock())
    orch._frames.roi_config = registry.get_default()
    orch._ordering.buffer = ReorderBuffer()
    orch._broadcaster.queue = asyncio.Queue()
    orch._frames.ocr_engine = SimpleNamespace(t_zero_detected=False)
    orch._frames.run_engine_analysis = AsyncMock(side_effect=lambda frame: _engine_result())
    orch._frames.run_jpeg_encoding = AsyncMock(return_value=None)
    orch.timeline.set_mission("ift-12", [
        {"id": "maxq", "t": 58, "event": "Max Q", "vehicle": "both", "auto": {"kind": "hud", "label": "MAX Q"}},
        {"id": "go", "t": -30, "event": "GO for launch", "vehicle": "both", "auto": {"kind": "manual"}},
    ])
    return orch


def _timeline_messages(orch):
    return [c.args[0]["payload"] for c in orch._broadcast.await_args_list
            if c.args[0]["type"] == "timeline_status"]


async def _send_queued(orch):
    """Run the broadcaster's after-telemetry hook for every queued telemetry message."""
    while not orch._broadcaster.queue.empty():
        message = orch._broadcaster.queue.get_nowait()
        if message["type"] == "telemetry":
            await orch._on_telemetry_sent(message["payload"])


async def test_hud_label_read_after_t_zero_confirms_its_event(orchestrator):
    orch = orchestrator
    orch._frames.ocr_engine = SimpleNamespace(t_zero_detected=True)
    reads = iter([
        OCRResult(**{**vars(OCRResult.unavailable(time=available("T+00:01:02", "T+00:01:02"))),
                     "timeline_below": available("MAX 0")}),
        OCRResult.unavailable(time=available("T+00:01:03", "T+00:01:03")),
    ])
    regions_seen = []

    async def ocr(frame, regions):
        regions_seen.append(set(regions))
        return next(reads)

    orch._frames.run_ocr_extraction = ocr
    for seq, frame_number in ((1, 30 * 90), (2, 30 * 91)):
        await orch._frames.process_frame(np.zeros((4, 4, 3), np.uint8), seq, frame_number, 30.0, detect_t_zero=False)
    await _send_queued(orch)

    assert "timeline_below" in regions_seen[0] and "timeline_below" not in regions_seen[1]
    status = orch.timeline.machine.status("maxq")
    assert status.status == "success"
    assert status.evidence == 'HUD milestone MAX Q (read "MAX 0")'
    assert status.met == 62
    last = {e["event_id"]: e for e in _timeline_messages(orch)[-1]["events"]}
    assert last["maxq"]["status"] == "success"


async def test_noise_and_unknown_labels_change_nothing(orchestrator):
    orch = orchestrator
    orch._frames.ocr_engine = SimpleNamespace(t_zero_detected=True)
    result = OCRResult(**{**vars(OCRResult.unavailable(time=available("T+00:01:02", "T+00:01:02"))),
                          "timeline_below": available("3"), "stage_sep_text": available("SECO")})
    orch._frames.run_ocr_extraction = AsyncMock(return_value=result)
    await orch._frames.process_frame(np.zeros((4, 4, 3), np.uint8), 1, 30 * 91, 30.0, detect_t_zero=False)
    await _send_queued(orch)
    assert orch.timeline.machine.status("maxq").status == "not_yet"
    assert orch._hud_labels == {}


async def test_stage_separation_still_comes_from_stage_sep_text(orchestrator):
    orch = orchestrator
    orch._frames.ocr_engine = SimpleNamespace(t_zero_detected=True)
    result = OCRResult(**{**vars(OCRResult.unavailable(time=available("T+00:02:30", "T+00:02:30"))),
                          "stage_sep_text": available("STAGE SEP")})
    orch._frames.run_ocr_extraction = AsyncMock(return_value=result)
    await orch._frames.process_frame(np.zeros((4, 4, 3), np.uint8), 1, 30 * 180, 30.0, detect_t_zero=False)
    assert orch.state.separation_state == SeparationState.POST_SEPARATION


async def test_countdown_reads_tick_the_timeline_before_t_zero(orchestrator):
    orch = orchestrator
    clock = iter(["00:00:27", "00:00:26", "00:00:25"])

    async def ocr(frame, regions):
        assert {"countdown_time", "time_sign"} <= set(regions)
        return OCRResult(**{**vars(OCRResult.unavailable()),
                            "countdown_clock": available(next(clock)), "time_sign": available("T-", "-")})

    orch._frames.run_ocr_extraction = ocr
    for seq in (1, 2, 3):
        await orch._frames.process_frame(np.zeros((4, 4, 3), np.uint8), seq, 30 * (seq + 1), 30.0, detect_t_zero=True)

    snapshots = _timeline_messages(orch)
    assert [s["met"] for s in snapshots] == [-26.0, -25.0]
    assert all(s["clock_source"] == "countdown" and s["hold"] is False for s in snapshots)
    assert orch.timeline.machine.status("go").status == "pending"
    # no record got a MET from the counting-down HUD clock
    while not orch._broadcaster.queue.empty():
        message = orch._broadcaster.queue.get_nowait()
        if message["type"] == "telemetry":
            assert message["payload"]["mission_elapsed_time"] is None


async def test_plus_sign_before_t_zero_detection_is_not_a_countdown(orchestrator):
    orch = orchestrator

    async def ocr(frame, regions):
        return OCRResult(**{**vars(OCRResult.unavailable()),
                            "countdown_clock": available("00:00:05"), "time_sign": available("T+", "+")})

    orch._frames.run_ocr_extraction = ocr
    for seq in (1, 2, 3):
        await orch._frames.process_frame(np.zeros((4, 4, 3), np.uint8), seq, 30 * seq, 30.0, detect_t_zero=True)
    assert _timeline_messages(orch) == []
