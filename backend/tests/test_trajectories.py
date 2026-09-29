"""Tests for historical ship trajectory loading and the /api/trajectories endpoints."""

from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from hypothesis import given, strategies as st

from src import trajectories
from src.server import create_app

HEADER = "polled_at,ship,gps_time,mission_time,latitude,longitude,altitude,speed\n"


def write_csv(directory: Path, name: str, rows: list[str]) -> Path:
    path = directory / name
    path.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


class TestListTrajectories:
    def test_lists_ship_files_sorted_by_number(self, tmp_path: Path):
        write_csv(tmp_path, "positions_ship41.csv", [])
        write_csv(tmp_path, "positions_ship9.csv", [])
        write_csv(tmp_path, "notes.csv", [])
        (tmp_path / "positions_ship50.json").write_text("{}")

        assert trajectories.list_trajectories(tmp_path) == [
            {"name": "ship9", "number": 9, "label": "Ship 9"},
            {"name": "ship41", "number": 41, "label": "Ship 41"},
        ]

    def test_missing_directory_returns_empty(self, tmp_path: Path):
        assert trajectories.list_trajectories(tmp_path / "nope") == []


class TestLoadTrajectory:
    def test_drops_negative_altitude_and_repeated_polls(self, tmp_path: Path):
        write_csv(tmp_path, "positions_ship39.csv", [
            "t,ship39,100.0,-2.5,25.99,-97.15,96.5,0",
            "t,ship39,100.0,-2.5,25.99,-97.15,96.5,0",   # repeated poll
            "t,ship39,110.0,7.5,25.90,-97.00,1500.0,0",
            "t,ship39,105.0,2.5,25.95,-97.10,800.0,0",    # out of order
            "t,ship39,120.0,17.5,,-96.90,2500.0,0",       # missing latitude
            "t,ship39,130.0,27.5,-17.60,106.72,-4.3,0",   # below ground
        ])

        points = trajectories.load_trajectory("ship39", tmp_path)

        assert points == [
            {"gps_time": 100.0, "mission_time": -2.5, "latitude": 25.99, "longitude": -97.15, "altitude": 96.5},
            {"gps_time": 110.0, "mission_time": 7.5, "latitude": 25.90, "longitude": -97.00, "altitude": 1500.0},
        ]

    def test_unknown_or_unsafe_names_return_none(self, tmp_path: Path):
        write_csv(tmp_path, "positions_ship39.csv", [])
        assert trajectories.load_trajectory("ship40", tmp_path) is None
        assert trajectories.load_trajectory("../positions_ship39", tmp_path) is None
        assert trajectories.load_trajectory("ship39.csv", tmp_path) is None

    @given(altitudes=st.lists(st.floats(min_value=-1e6, max_value=1e6), max_size=30))
    def test_never_returns_below_ground_points(self, tmp_path_factory, altitudes: list[float]):
        directory = tmp_path_factory.mktemp("traj")
        write_csv(directory, "positions_ship1.csv", [
            f"t,ship1,{i},{i},0,0,{alt!r},0" for i, alt in enumerate(altitudes)
        ])
        points = trajectories.load_trajectory("ship1", directory)
        assert points is not None
        assert [p["altitude"] for p in points] == [a for a in altitudes if a >= 0]


@pytest.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestTrajectoryEndpoints:
    async def test_list_and_fetch(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        write_csv(tmp_path, "positions_ship39.csv", ["t,ship39,100.0,1.0,25.99,-97.15,96.5,0"])
        monkeypatch.setattr(trajectories, "TRAJECTORIES_DIR", tmp_path)

        listing = await client.get("/api/trajectories")
        assert listing.status_code == 200
        assert listing.json() == [{"name": "ship39", "number": 39, "label": "Ship 39"}]

        detail = await client.get("/api/trajectories/ship39")
        assert detail.status_code == 200
        assert detail.json()[0]["latitude"] == 25.99

    async def test_unknown_trajectory_is_404(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(trajectories, "TRAJECTORIES_DIR", tmp_path)
        response = await client.get("/api/trajectories/ship999")
        assert response.status_code == 404
