"""Tests for previous-flight loading and the /api/previous-flights endpoints."""

import json
import os
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from src import previous_flights
from src.server import create_app


def record(sign: str, seconds: int, ss_speed=None, sh_speed=None, ss_alt=None, sh_alt=None) -> dict:
    def vehicle(speed, altitude):
        return {"speed": speed, "altitude": altitude, "fuel": {}, "engines": {}}

    return {
        "frame_number": 0,
        "vehicles": {
            "starship": vehicle(ss_speed, ss_alt),
            "superheavy": vehicle(sh_speed, sh_alt),
        },
        "time": {"sign": sign, "hours": 0, "minutes": 1, "seconds": seconds},
        "real_time_seconds": 60.0 + seconds,
        "real_time": {},
    }


def write_flight(directory: Path, name: str, records: list[dict]) -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(records, indent=4), encoding="utf-8")
    return path


class TestToColumns:
    def test_reduces_records_to_plotted_columns(self):
        columns = previous_flights.to_columns([
            record("T", 5, ss_speed=100, sh_speed=90, ss_alt=1.5),
            record("-", 3),
        ])
        assert columns == {
            "met": ["+00:01:05", "-00:01:03"],
            "t": [65.0, 63.0],
            "ss_speed": [100, None],
            "sh_speed": [90, None],
            "ss_alt": [1.5, None],
            "sh_alt": [None, None],
        }

    def test_drops_records_without_a_complete_met(self):
        incomplete = record("+", 1)
        incomplete["time"]["minutes"] = None
        assert previous_flights.to_columns([incomplete, {"vehicles": {}}])["met"] == []


class TestLoadFlight:
    def test_unknown_or_unsafe_names_return_none(self, tmp_path: Path):
        write_flight(tmp_path, "ift-1", [record("+", 1)])
        assert previous_flights.load_flight("ift-2", tmp_path) is None
        # Path separators and dots are stripped, so this looks for "ift-1json"
        assert previous_flights.load_flight("../ift-1.json", tmp_path) is None

    def test_caches_until_the_file_changes(self, tmp_path: Path):
        path = write_flight(tmp_path, "ift-1", [record("+", 1, ss_speed=1)])
        first = previous_flights.load_flight("ift-1", tmp_path)
        assert previous_flights.load_flight("ift-1", tmp_path) is first

        write_flight(tmp_path, "ift-1", [record("+", 1, ss_speed=2)])
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        second = previous_flights.load_flight("ift-1", tmp_path)
        assert json.loads(first)["ss_speed"] == [1]
        assert json.loads(second)["ss_speed"] == [2]


@pytest.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestPreviousFlightEndpoints:
    async def test_list_and_fetch_gzipped_columns(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        write_flight(tmp_path, "ift-7", [record("T", s % 60, ss_speed=s * 10) for s in range(200)])
        monkeypatch.setattr(previous_flights, "PREVIOUS_FLIGHTS_DIR", tmp_path)

        listing = await client.get("/api/previous-flights")
        assert listing.json() == [{"name": "IFT 7", "filename": "ift-7"}]

        detail = await client.get("/api/previous-flights/ift-7", headers={"Accept-Encoding": "gzip"})
        assert detail.status_code == 200
        assert detail.headers["content-encoding"] == "gzip"
        assert detail.json()["ss_speed"][:3] == [0, 10, 20]

    async def test_unknown_flight_is_404(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(previous_flights, "PREVIOUS_FLIGHTS_DIR", tmp_path)
        response = await client.get("/api/previous-flights/ift-99")
        assert response.status_code == 404
