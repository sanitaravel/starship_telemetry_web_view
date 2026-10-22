"""Pydantic request/response models for the REST endpoints."""

from typing import Any

from pydantic import BaseModel


class ValidateUrlRequest(BaseModel):
    """Request body for the /api/validate-url endpoint."""

    url: str


class ValidateUrlResponse(BaseModel):
    """Response body for the /api/validate-url endpoint."""

    valid: bool
    message: str
    url: str


class PipelineStatusResponse(BaseModel):
    """Response body for the /api/status endpoint."""

    status: str
    gpu: dict[str, Any]
    skip_frames: int
    current_sequence: int
