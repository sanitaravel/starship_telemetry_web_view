"""OCR result types: one field's read and a whole frame's worth of them."""

from dataclasses import dataclass, field

from src.core.enums import OCRFieldStatus


@dataclass
class OCRFieldResult:
    """Result of OCR extraction for a single text field."""

    status: OCRFieldStatus
    raw_text: str | None = None
    parsed_value: float | str | None = None


def _missing() -> OCRFieldResult:
    return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)


@dataclass
class OCRResult:
    """Aggregated OCR results for all text regions in a frame."""

    time: OCRFieldResult
    speed_l: OCRFieldResult
    speed_l_unit: OCRFieldResult
    speed_r: OCRFieldResult
    speed_r_unit: OCRFieldResult
    altitude_l: OCRFieldResult
    altitude_l_unit: OCRFieldResult
    altitude_r: OCRFieldResult
    altitude_r_unit: OCRFieldResult
    stage_l: OCRFieldResult
    stage_r: OCRFieldResult
    stage_sep_text: OCRFieldResult
    # Optional ROIs (absent from older templates and from most frames):
    # the HUD milestone label below the arc's line, read at a reduced rate
    timeline_below: OCRFieldResult = field(default_factory=_missing)
    # Before T-0 only: the countdown clock in the top-left corner, the sign
    # left of the HUD clock, and the HUD clock itself (kept apart from
    # ``time``, which stays unavailable until T-0 so no record gets a MET)
    countdown_time: OCRFieldResult = field(default_factory=_missing)
    time_sign: OCRFieldResult = field(default_factory=_missing)
    countdown_clock: OCRFieldResult = field(default_factory=_missing)

    @classmethod
    def unavailable(cls, time: OCRFieldResult | None = None) -> "OCRResult":
        """An OCRResult carrying no telemetry: every field UNAVAILABLE.

        Args:
            time: Optional time field to keep in place of UNAVAILABLE
                (e.g. the read that gated the rest of the frame out).
        """
        missing = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
        fields = {name: missing for name in cls.__dataclass_fields__}
        if time is not None:
            fields["time"] = time
        return cls(**fields)
