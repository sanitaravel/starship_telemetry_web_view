"""Tests for per-mission engine settings files and the /api/missions endpoints."""

import json
import os
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from pydantic import ValidationError

from src.server import create_app
from src.storage import missions
from src.storage.missions import MissionSettings, list_missions, load_mission, mission_id, save_mission


class TestMissionId:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("IFT 14", "ift-14"),  # same as results_previous/ift-14.json
            ("Flight 12", "flight-12"),
            ("  IFT-14  ", "ift-14"),
            ("Ship 33 / Booster 15!", "ship-33-booster-15"),
            ("!!!", ""),
        ],
    )
    def test_maps_names_to_file_stems(self, name: str, expected: str):
        assert mission_id(name) == expected

    def test_is_capped(self):
        assert len(mission_id("x" * 200)) == 64


class TestMissionSettings:
    def test_strips_names(self):
        settings = MissionSettings(mission="  IFT 14 ", ship=" S35 ")
        assert (settings.mission, settings.ship) == ("IFT 14", "S35")

    @pytest.mark.parametrize(
        "fields",
        [
            {"mission": ""},
            {"mission": "!!!"},
            {"mission": "IFT 14", "starship_engines": {"sh_e1": "R1"}},  # not a Starship engine
            {"mission": "IFT 14", "starship_engines": {"ss_e1": "r576"}},  # not normalised
            {"mission": "IFT 14", "starship_engines": {"ss_e1": "R" * 9}},  # too long
            {"mission": "IFT 14", "starship_engines": {"ss_e1": "../x"}},
            {"mission": "IFT 14", "superheavy_engines": {"sh_e34": "R1"}},  # only 33 engines
            {"mission": "IFT 14", "superheavy_engines": {"ss_e1": "R1"}},  # a Starship engine
            {"mission": "IFT 14", "booster": "B" * 41},
        ],
    )
    def test_rejects_invalid(self, fields: dict):
        with pytest.raises(ValidationError):
            MissionSettings(**fields)


class TestFiles:
    def test_one_file_per_mission(self, tmp_path: Path):
        save_mission(MissionSettings(mission="IFT 14", ship="S35", starship_engines={"ss_e6": "R576"}), tmp_path)
        save_mission(MissionSettings(mission="IFT 15", ship="S36"), tmp_path)

        assert sorted(p.name for p in tmp_path.iterdir()) == ["engines_ift-14.json", "engines_ift-15.json"]
        saved = json.loads((tmp_path / "engines_ift-14.json").read_text(encoding="utf-8"))
        assert saved["mission"] == "IFT 14"
        assert saved["ship"] == "S35"
        assert saved["starship_engines"] == {"ss_e6": "R576"}
        assert saved["updated_at"]

    def test_load_returns_what_was_saved(self, tmp_path: Path):
        save_mission(MissionSettings(mission="IFT 14", ship="S35", starship_engines={"ss_e1": "R101"}), tmp_path)

        record = load_mission("ift-14", tmp_path)
        assert record["id"] == "ift-14"
        assert record["mission"] == "IFT 14"
        assert record["starship_engines"] == {"ss_e1": "R101"}

    def test_saving_again_replaces_the_file(self, tmp_path: Path):
        save_mission(MissionSettings(mission="IFT 14", starship_engines={"ss_e1": "R101"}), tmp_path)
        save_mission(MissionSettings(mission="ift 14", starship_engines={"ss_e2": "R202"}), tmp_path)

        assert [p.name for p in tmp_path.iterdir()] == ["engines_ift-14.json"]  # no temp file left behind
        assert load_mission("ift-14", tmp_path)["starship_engines"] == {"ss_e2": "R202"}

    def test_list_is_newest_first_and_skips_bad_files(self, tmp_path: Path):
        save_mission(MissionSettings(mission="IFT 14", ship="S35"), tmp_path)
        save_mission(MissionSettings(mission="IFT 15", ship="S36"), tmp_path)
        # Make IFT 14 the most recent
        older = json.loads((tmp_path / "engines_ift-15.json").read_text(encoding="utf-8"))
        older["updated_at"] = "2020-01-01T00:00:00+00:00"
        (tmp_path / "engines_ift-15.json").write_text(json.dumps(older), encoding="utf-8")
        (tmp_path / "engines_broken.json").write_text("{not json", encoding="utf-8")

        listing = list_missions(tmp_path)
        assert [(m["id"], m["mission"], m["ship"]) for m in listing] == [
            ("ift-14", "IFT 14", "S35"),
            ("ift-15", "IFT 15", "S36"),
        ]
        assert "starship_engines" not in listing[0]

    def test_both_vehicles_are_saved_in_one_file(self, tmp_path: Path):
        save_mission(
            MissionSettings(
                mission="IFT 14",
                ship="S41",
                booster="B15",
                starship_engines={"ss_e6": "R171"},
                superheavy_engines={"sh_e1": "R301", "sh_e33": "R333"},
            ),
            tmp_path,
        )

        record = load_mission("ift-14", tmp_path)
        assert (record["ship"], record["booster"]) == ("S41", "B15")
        assert record["superheavy_engines"] == {"sh_e1": "R301", "sh_e33": "R333"}
        assert list_missions(tmp_path)[0]["booster"] == "B15"
        assert "superheavy_engines" not in list_missions(tmp_path)[0]

    def test_reads_a_file_saved_before_super_heavy_settings(self, tmp_path: Path):
        (tmp_path / "engines_ift-14.json").write_text(
            json.dumps({"mission": "IFT 14", "ship": "S41", "starship_engines": {"ss_e1": "R172"},
                        "updated_at": "2026-10-20T12:55:30+00:00"}),
            encoding="utf-8",
        )

        record = load_mission("ift-14", tmp_path)
        assert record["starship_engines"] == {"ss_e1": "R172"}
        assert (record["booster"], record["superheavy_engines"]) == ("", {})

    def test_never_touches_a_flight_file_of_the_same_name(self, tmp_path: Path):
        # e.g. a copy of results_previous/ift-14.json sitting next to the settings
        flight = tmp_path / "ift-14.json"
        flight.write_text('[{"frame_number": 0}]', encoding="utf-8")
        before = flight.read_bytes()

        save_mission(MissionSettings(mission="IFT 14", starship_engines={"ss_e6": "R576"}), tmp_path)

        assert flight.read_bytes() == before
        assert (tmp_path / "engines_ift-14.json").is_file()
        assert [m["id"] for m in list_missions(tmp_path)] == ["ift-14"]  # the flight is not listed

    def test_missing_directory_lists_nothing(self, tmp_path: Path):
        assert list_missions(tmp_path / "nope") == []

    @pytest.mark.parametrize("identifier", ["ift-99", "../secrets", "IFT-14", "a/b", ""])
    def test_unknown_or_unsafe_ids_load_nothing(self, tmp_path: Path, identifier: str):
        save_mission(MissionSettings(mission="IFT 14"), tmp_path)
        assert load_mission(identifier, tmp_path) is None


@pytest.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestMissionEndpoints:
    async def test_save_list_and_fetch(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(missions, "MISSIONS_DIR", tmp_path)

        saved = await client.put(
            "/api/missions/ift-14",
            json={"mission": "IFT 14", "ship": "S35", "starship_engines": {"ss_e6": "R576"}},
        )
        assert saved.status_code == 200
        assert saved.json()["id"] == "ift-14"
        assert (tmp_path / "engines_ift-14.json").is_file()

        listing = await client.get("/api/missions")
        assert [(m["id"], m["ship"]) for m in listing.json()] == [("ift-14", "S35")]

        detail = await client.get("/api/missions/ift-14")
        assert detail.json()["starship_engines"] == {"ss_e6": "R576"}

    async def test_unknown_mission_is_404(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(missions, "MISSIONS_DIR", tmp_path)
        response = await client.get("/api/missions/ift-99")
        assert response.status_code == 404

    async def test_id_must_match_the_name(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(missions, "MISSIONS_DIR", tmp_path)
        response = await client.put("/api/missions/ift-15", json={"mission": "IFT 14"})
        assert response.status_code == 400
        assert not os.listdir(tmp_path)

    async def test_invalid_settings_are_rejected(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(missions, "MISSIONS_DIR", tmp_path)
        response = await client.put(
            "/api/missions/ift-14", json={"mission": "IFT 14", "starship_engines": {"ss_e9": "R1"}}
        )
        assert response.status_code == 422
        assert not os.listdir(tmp_path)
