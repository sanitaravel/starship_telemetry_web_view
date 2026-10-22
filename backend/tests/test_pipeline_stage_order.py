"""Stage assignment in the pipeline runs in video order, not completion order."""

import asyncio
from types import SimpleNamespace

from src.core.enums import OCRFieldStatus
from src.core.gpu import AccelerationBackend, GPUCapabilities
from src.ocr.results import OCRFieldResult, OCRResult
from src.pipeline import PipelineOrchestrator
from src.pipeline.reorder_buffer import ReorderBuffer
from src.templates.template_registry import TemplateRegistry, load_default_template

_NONE = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)


def _field(text):
    return OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text=text, parsed_value=text)


def _ocr(left_label=None, right_label=None, sep=False):
    """Both sides show readings; labels and STAGE SEP optional."""
    return OCRResult(
        time=_NONE,
        speed_l=_field("1000"), speed_l_unit=_field("KM/H"),
        speed_r=_field("7000"), speed_r_unit=_field("KM/H"),
        altitude_l=_field("80"), altitude_l_unit=_field("KM"),
        altitude_r=_field("90"), altitude_r_unit=_field("KM"),
        stage_l=_field(left_label) if left_label else _NONE,
        stage_r=_field(right_label) if right_label else _NONE,
        stage_sep_text=_field("STAGE SEP") if sep else _NONE,
    )


class _Record(SimpleNamespace):
    def model_dump(self):
        return dict(vars(self))


async def test_frames_finishing_out_of_order_are_assigned_in_video_order():
    registry = TemplateRegistry()
    load_default_template(registry)
    cpu = GPUCapabilities(backend=AccelerationBackend.CPU, device_name=None,
                          cuda_version=None, gpu_available=False)
    orchestrator = PipelineOrchestrator(cpu, registry, broadcast=None)
    orchestrator._reorder_buffer = ReorderBuffer()
    orchestrator._broadcast_queue = asyncio.Queue()

    frames = {
        1: _ocr("SUPER HEAVY", "STARSHIP", sep=True),  # separation, labels read
        2: _ocr(),  # labels unreadable: each side keeps its vehicle
        3: _ocr(),
    }
    # Frames 3 and 2 finish before frame 1
    for seq in (3, 2, 1):
        record = _Record(sequence_number=0, stage_assignment_left="?", stage_assignment_right="?",
                         separation_state="?", stage_sep_found=False)
        await orchestrator._submit_to_reorder_buffer(seq, record, None, frames[seq])

    sent = []
    while not orchestrator._broadcast_queue.empty():
        message = orchestrator._broadcast_queue.get_nowait()
        if message["type"] == "telemetry":
            sent.append(message["payload"])

    assert [m["sequence_number"] for m in sent] == [1, 2, 3]
    for m in sent:
        assert (m["stage_assignment_left"], m["stage_assignment_right"]) == ("super_heavy", "starship")
        assert m["separation_state"] == "post_separation"
        assert m["stage_sep_found"] is True
