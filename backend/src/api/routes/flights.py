"""Previous flight endpoints: /api/previous-flights[/{name}]."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from src.storage import previous_flights

router = APIRouter()

# Plain `def` handlers run in the threadpool, so reading a flight file
# never blocks the event loop (and the live telemetry stream with it).


@router.get("/api/previous-flights")
def list_previous_flights() -> list[dict[str, str]]:
    """List available previous flight data files.

    Returns:
        List of objects with 'name' (display name) and 'filename' fields.
    """
    return previous_flights.list_flights(previous_flights.PREVIOUS_FLIGHTS_DIR)


@router.get("/api/previous-flights/{name}")
def get_previous_flight(name: str) -> Response:
    """Serve a previous flight as columnar JSON.

    Args:
        name: The filename stem (e.g., 'ift-13') of the flight data.

    Returns:
        ``{"met": [...], "t": [...], "ss_speed": [...], "sh_speed": [...],
        "ss_alt": [...], "sh_alt": [...]}``, one entry per record.
    """
    payload = previous_flights.load_flight(name, previous_flights.PREVIOUS_FLIGHTS_DIR)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Flight data '{name}' not found."},
        )
    return Response(content=payload, media_type="application/json")
