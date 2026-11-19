"""Report a problem, "Bumper or ad problem": the ads and bumpers just played, picked by name (#90)."""

import datetime
from types import SimpleNamespace

import pytest

from fs42.block_plan import BlockPlanEntry
from fs42_live import reports


@pytest.fixture
def station(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime").mkdir()
    for f in ("catalog/ch/commercial/80s/Ad A.mp4", "catalog/ch/commercial/80s/Ad B.mp4",
              "catalog/ch/bump/post/Station ID.mp4", "catalog/ch/shows/Show.mkv", "elsewhere/Ad C.mp4"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"x")
    return {"content_dir": "catalog/ch", "commercial_dir": "commercial", "bump_dir": "bump",
            "network_name": "ch", "channel_number": 3, "network_type": "standard"}


def schedule(monkeypatch, start, plan):
    """A schedule playing plan (BlockPlanEntry list) from start, once."""
    ends, at = [], start
    for e in plan:
        at += datetime.timedelta(seconds=e.duration)
        ends.append(at)

    class FakeManager:
        def get_play_point(self, network, when):
            t = start
            for i, e in enumerate(plan):
                if when < ends[i]:
                    return SimpleNamespace(plan=plan, index=i, offset=(when - t).total_seconds())
                t = ends[i]
            raise ValueError("no schedule")

    monkeypatch.setattr(reports, "LiquidManager", FakeManager)


def test_recent_clips_latest_first_each_once(station, monkeypatch):
    start = datetime.datetime(2026, 11, 14, 20, 0, 0)
    plan = [BlockPlanEntry("catalog/ch/shows/Show.mkv", 0, 600),
            BlockPlanEntry("catalog/ch/bump/post/Station ID.mp4", 0, 5, content_type="bump"),
            BlockPlanEntry("catalog/ch/commercial/80s/Ad A.mp4", 0, 30, content_type="commercial"),
            BlockPlanEntry("catalog/ch/commercial/80s/Ad B.mp4", 0, 30, content_type="commercial"),
            BlockPlanEntry("catalog/ch/commercial/80s/Ad A.mp4", 0, 30, content_type="commercial"),
            BlockPlanEntry("catalog/ch/commercial/80s/Gone.mp4", 0, 30, content_type="commercial"),
            BlockPlanEntry("catalog/ch/shows/Show.mkv", 600, 600)]
    schedule(monkeypatch, start, plan)
    until = start + datetime.timedelta(seconds=600 + 5 + 30 * 4 + 60)  # a minute into the show again
    clips = reports.recent_clips("ch", until)
    assert [c["name"] for c in clips] == ["Ad A", "Ad B", "Station ID"]  # Gone.mp4 isn't there any more
    assert clips[2] == {"file": "catalog/ch/bump/post/Station ID.mp4", "name": "Station ID", "kind": "bump"}


def test_recent_clips_leaves_out_one_just_started(station, monkeypatch):
    start = datetime.datetime(2026, 11, 14, 20, 0, 0)
    plan = [BlockPlanEntry("catalog/ch/commercial/80s/Ad A.mp4", 0, 30, content_type="commercial"),
            BlockPlanEntry("catalog/ch/commercial/80s/Ad B.mp4", 0, 30, content_type="commercial")]
    schedule(monkeypatch, start, plan)
    until = start + datetime.timedelta(seconds=32)  # Ad B started 2 seconds ago: not on the screen yet
    assert [c["name"] for c in reports.recent_clips("ch", until)] == ["Ad A"]


def test_is_clip_only_the_stations_ads_and_bumpers(station):
    assert reports.is_clip(station, "catalog/ch/commercial/80s/Ad A.mp4") == "commercial"
    assert reports.is_clip(station, "catalog/ch/bump/post/Station ID.mp4") == "bump"
    assert reports.is_clip(station, "catalog/ch/shows/Show.mkv") is None
    assert reports.is_clip(station, "elsewhere/Ad C.mp4") is None
    assert reports.is_clip(station, "catalog/ch/commercial/../../../elsewhere/Ad C.mp4") is None
    assert reports.is_clip(station, "catalog/ch/commercial/80s/Missing.mp4") is None


def test_save_keeps_the_clip_and_issue(station, monkeypatch):
    monkeypatch.setattr(reports, "what_played", lambda network, until: [])
    saved = reports.save(station, 1, "", 2, "tv", None, "catalog/ch/commercial/80s/Ad A.mp4", 1)
    assert saved["clip"] == {"file": "catalog/ch/commercial/80s/Ad A.mp4", "name": "Ad A", "kind": "commercial"}
    assert saved["issue"] == "Take it off this box"
    assert reports.recent(1)[0]["clip"]["name"] == "Ad A"


def test_save_keeps_the_players_picture_notes(station, monkeypatch):
    monkeypatch.setattr(reports, "what_played", lambda network, until: [])
    notes = [f"1:0{i} picture 1.{i} s behind the sound" for i in range(15)] + ["x" * 500]
    saved = reports.save(station, 2, "", 2, "phone", player=notes)
    assert len(saved["player"]) == 12 and saved["player"][0] == notes[4] and len(saved["player"][-1]) == 300
    assert reports.recent(1)[0]["player"] == saved["player"]
    assert "player" not in reports.save(station, 2, "", 2, "phone", player=[])
    assert "player" not in reports.save(station, 2, "", 2, "phone", player="not a list")
