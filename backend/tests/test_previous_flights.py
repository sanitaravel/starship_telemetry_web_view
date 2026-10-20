"""Tests for previous-flight loading and the /api/previous-flights endpoints."""

import json
import os
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from hypothesis import given, settings
from hypothesis import strategies as st

from src import previous_flights
from src.server import create_app


def record(
    sign: str,
    seconds: int,
    ss_speed=None,
    sh_speed=None,
    ss_alt=None,
    sh_alt=None,
    ss_engines=None,
    sh_engines=None,
) -> dict:
    def vehicle(speed, altitude, engines):
        return {"speed": speed, "altitude": altitude, "fuel": {}, "engines": engines or {}}

    return {
        "frame_number": 0,
        "vehicles": {
            "starship": vehicle(ss_speed, ss_alt, ss_engines),
            "superheavy": vehicle(sh_speed, sh_alt, sh_engines),
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
            "engines": [],
        }

    def test_drops_records_without_a_complete_met(self):
        incomplete = record("+", 1)
        incomplete["time"]["minutes"] = None
        assert previous_flights.to_columns([incomplete, {"vehicles": {}}])["met"] == []


class TestEngineChanges:
    SH_ON = {"sh_e1": "active", "sh_e2": "active"}
    SH_ONE_OUT = {"sh_e1": "active", "sh_e2": "inactive"}
    SS_OFF = {"ss_e1": "inactive", "ss_e2": "inactive"}

    def test_empty_engine_maps_emit_nothing(self):
        columns = previous_flights.to_columns([record("T", s) for s in range(3)])
        assert columns["engines"] == []

    def test_emits_a_vehicle_map_only_when_it_changes(self):
        columns = previous_flights.to_columns([
            record("T", 0, sh_engines=self.SH_ON),
            record("T", 1, sh_engines=self.SH_ON, ss_engines=self.SS_OFF),
            record("T", 2, sh_engines=self.SH_ON, ss_engines=self.SS_OFF),
            # An empty map means no reading, not a change
            record("T", 3, ss_engines=self.SS_OFF),
            record("T", 4, sh_engines=self.SH_ONE_OUT, ss_engines=self.SS_OFF),
            record("T", 5, sh_engines=self.SH_ON, ss_engines=self.SS_OFF),
        ])
        assert columns["engines"] == [
            {"i": 0, "sh": self.SH_ON},
            {"i": 1, "ss": self.SS_OFF},
            {"i": 4, "sh": self.SH_ONE_OUT},
            {"i": 5, "sh": self.SH_ON},
        ]

    def test_row_index_skips_records_without_met(self):
        no_met = record("T", 1, sh_engines=self.SH_ONE_OUT)
        no_met["time"] = {}
        columns = previous_flights.to_columns([
            record("T", 0, sh_engines=self.SH_ON),
            no_met,
            record("T", 2, sh_engines=self.SH_ON),
            record("T", 3, sh_engines=self.SH_ONE_OUT),
        ])
        assert columns["met"] == ["+00:01:00", "+00:01:02", "+00:01:03"]
        assert columns["engines"] == [
            {"i": 0, "sh": self.SH_ON},
            {"i": 2, "sh": self.SH_ONE_OUT},
        ]


def _expand_engine_changes(columns: dict) -> list[dict]:
    """Per-row engine maps from the change list, as the dashboard replays it:
    each vehicle's map holds until its next entry."""
    current = {"ss": None, "sh": None}
    changes = iter(columns["engines"])
    pending = next(changes, None)
    rows = []
    for i in range(len(columns["met"])):
        while pending is not None and pending["i"] <= i:
            for prefix in ("ss", "sh"):
                if prefix in pending:
                    current[prefix] = pending[prefix]
            pending = next(changes, None)
        rows.append(dict(current))
    return rows


_STATUS = st.sampled_from(["active", "inactive", "undetected"])
_SH_MAP = st.one_of(st.just({}), st.fixed_dictionaries({f"sh_e{n}": _STATUS for n in range(1, 34)}))
_SS_MAP = st.one_of(st.just({}), st.fixed_dictionaries({f"ss_e{n}": _STATUS for n in range(1, 7)}))


class TestEngineChangeRoundTrip:
    @given(
        st.lists(
            st.tuples(st.booleans(), _SS_MAP, _SH_MAP, st.integers(min_value=1, max_value=4)),
            max_size=40,
        )
    )
    @settings(deadline=None)
    def test_replaying_the_change_list_restores_each_rows_last_reading(self, runs):
        records, expected = [], []
        last = {"ss": None, "sh": None}
        second = 0
        for has_met, ss, sh, repeat in runs:
            for _ in range(repeat):
                rec = record("T", second % 60, ss_engines=ss, sh_engines=sh)
                second += 1
                if not has_met:
                    rec["time"] = {}
                    records.append(rec)
                    continue
                records.append(rec)
                # An empty map is "no reading": the row keeps the last one
                if ss:
                    last["ss"] = ss
                if sh:
                    last["sh"] = sh
                expected.append(dict(last))
        columns = previous_flights.to_columns(records)
        assert len(columns["met"]) == len(expected)
        assert _expand_engine_changes(columns) == expected
        # Entries are in row order, one per row at most, never empty
        indices = [c["i"] for c in columns["engines"]]
        assert indices == sorted(set(indices))
        assert all(set(c) - {"i"} for c in columns["engines"])

    def test_results_previous_file_with_engines_round_trips_through_load_flight(self, tmp_path: Path):
        sh_all = {f"sh_e{n}": "active" for n in range(1, 34)}
        sh_out = {**sh_all, "sh_e20": "inactive"}
        sh_hidden = {f"sh_e{n}": "undetected" for n in range(1, 34)}
        ss_off = {f"ss_e{n}": "inactive" for n in range(1, 7)}
        ss_on = {f"ss_e{n}": "active" for n in range(1, 7)}
        records = [
            record("T", 0, ss_speed=100, sh_speed=100, ss_engines=ss_off, sh_engines=sh_all),
            record("T", 1, ss_speed=110, sh_speed=110, ss_engines=ss_off, sh_engines=sh_all),
            record("T", 2, ss_speed=120, sh_speed=120, ss_engines=ss_off, sh_engines=sh_hidden),
            record("T", 3, ss_speed=130, sh_speed=None, ss_engines=ss_on, sh_engines=sh_out),
            record("T", 4, ss_speed=140, sh_speed=90, ss_engines=ss_on, sh_engines=sh_out),
        ]
        write_flight(tmp_path, "ift-99", records)
        payload = json.loads(previous_flights.load_flight("ift-99", tmp_path))
        assert payload["met"] == [f"+00:01:0{s}" for s in range(5)]
        assert payload["engines"] == [
            {"i": 0, "ss": ss_off, "sh": sh_all},
            {"i": 2, "sh": sh_hidden},
            {"i": 3, "ss": ss_on, "sh": sh_out},
        ]
        assert payload["sh_speed"] == [100, 110, 120, None, 90]


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
