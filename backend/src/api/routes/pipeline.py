"""Pipeline endpoints: GET /api/status and POST /api/validate-url."""

from typing import Any

from fastapi import APIRouter

from src.api.runtime import orchestrator
from src.api.schemas import ValidateUrlRequest, ValidateUrlResponse

router = APIRouter()


@router.get("/api/status")
async def get_pipeline_status() -> dict[str, Any]:
    """Return current pipeline status and GPU capabilities.

    Returns:
        Dictionary with status, gpu info, skip_frames, current_sequence,
        the parallel-processing counters and the backend version.
    """
    return orchestrator.build_status_payload()


@router.post("/api/validate-url")
async def validate_url(request: ValidateUrlRequest) -> ValidateUrlResponse:
    """Validate a livestream URL is reachable.

    Args:
        request: Request body containing the URL to validate.

    Returns:
        Validation result with valid flag, message, and the URL.
    """
    url = request.url
    if not url or not url.strip():
        return ValidateUrlResponse(
            valid=False,
            message="URL cannot be empty.",
            url=url,
        )

    error = await orchestrator.frame_extractor.validate(url)
    if error is None:
        return ValidateUrlResponse(
            valid=True,
            message="URL is reachable and active.",
            url=url,
        )
    return ValidateUrlResponse(
        valid=False,
        message=error.message,
        url=url,
    )
