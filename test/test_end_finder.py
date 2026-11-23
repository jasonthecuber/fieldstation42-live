import datetime
import sqlite3
from types import SimpleNamespace as NS

from fs42.fluid_statements import FluidStatements
from fs42.reel_cutter import ReelCutter
from fs42_live import end_finder
from fs42_live.end_finder import end_of


def test_a_silent_logo_after_the_show_is_cut_off():
    # Family Feud: the show's last card, then ten silent seconds of an uploader's circle logo
    assert end_of(1320.0, [(1309.5, 1310.0)], [(1300.2, 1302.0), (1310.1, 1320.0)], 1230.0) \
        == (1309.5, "silent and black")
    assert end_of(1320.0, [], [(1310.1, 1320.0)], 1230.0) == (1310.1, "silent")


def test_black_then_a_silent_card_is_cut_where_the_black_starts():
    # the Burger Chef ad: black at 29.16, then a silent web address to the end
    assert end_of(33.48, [(29.16, 29.56)], [(29.2, 33.48)]) == (29.16, "silent and black")


def test_long_black_and_quiet_after_the_logos_goes():
    # The Andy Griffith Show: the CBS logos, then 11 s of nothing
    assert end_of(1530.0, [(1518.5, 1530.0)], [(1518.6, 1530.0)], 1440.0)[0] == 1518.5


def test_what_runs_to_the_end_is_kept():
    assert end_of(1500.0, [(700.0, 701.0)], [(700.0, 701.2)], 1410.0) == (1500.0, "")
    # less than a second of black isn't worth it
    assert end_of(1500.0, [(1499.4, 1500.0)], [], 1410.0) == (1500.0, "")


def test_long_silent_picture_may_be_silent_credits():
    assert end_of(1500.0, [], [(1460.0, 1500.0)], 1410.0) == (1500.0, "")
    # nor black with sound over it that long
    assert end_of(1500.0, [(1470.0, 1500.0)], [], 1410.0) == (1500.0, "")
    # but black and quiet together, any length
    assert end_of(1500.0, [(1470.0, 1500.0)], [(1470.2, 1500.0)], 1410.0)[0] == 1470.0


def test_quiet_all_along_isnt_an_ending():
    # a silent film: quiet from where the look began
    assert end_of(600.0, [(595.0, 600.0)], [(510.0, 600.0)], 510.0) == (595.0, "black")
    assert end_of(600.0, [], [(510.0, 600.0)], 510.0) == (600.0, "")


def test_never_more_than_half():
    assert end_of(20.0, [(8.0, 20.0)], [(8.0, 20.0)]) == (20.0, "")


def test_the_stored_end_is_for_that_file_only():
    db = sqlite3.connect(":memory:")
    FluidStatements.init_db(db)
    FluidStatements.add_play_end(db, "/a.mp4", 1309.5, 1320.0)
    assert FluidStatements.get_play_end(db, "/a.mp4", 1320.0) == 1309.5
    assert FluidStatements.get_play_end(db, "/a.mp4", 1500.0) is None  # replaced by another file
    assert FluidStatements.get_play_end(db, "/b.mp4", 1320.0) is None
    FluidStatements.add_play_end(db, "/c.mp4", 900.0, 900.0)  # looked at: runs to the end
    assert FluidStatements.get_play_end(db, "/c.mp4", 900.0) is None


def test_the_last_act_stops_at_the_end_found():
    clip = NS(path="/a.mp4", duration=1309.5, content_type="feature", media_type="video")
    reel = lambda: NS(make_plan=lambda: [NS(path="ad", duration=60, skip=0)])
    points = [{"chapter_start": 0.0, "chapter_end": 600.0}, {"chapter_start": 600.0, "chapter_end": 1320.0}]
    plan = ReelCutter.cut_reels_into_base(clip, [reel(), reel()], 0, 1309.5, "standard", None, None, points)
    acts = [e for e in plan if e.path == "/a.mp4"]
    assert [(a.skip, a.duration) for a in acts] == [(0.0, 600.0), (600.0, 709.5)]


def _finder(monkeypatch, stations, entries, found, ends, soon=None, cuts=None):
    looked, lengths, stored = [], {}, {}
    monkeypatch.setattr(end_finder, "FluidBuilder", lambda: None)
    monkeypatch.setattr(end_finder, "StationManager", lambda: NS(stations=stations))
    monkeypatch.setattr(end_finder, "CatalogAPI", NS(get_entries=lambda s: entries[s["network_name"]]))
    monkeypatch.setattr(end_finder, "_found", lambda f: dict(found))
    monkeypatch.setattr(end_finder, "_cuts", lambda f: dict(cuts or {}))
    monkeypatch.setattr(end_finder, "_length", lambda f, p: 1500.0)
    monkeypatch.setattr(end_finder, "find_end", lambda p, d: looked.append(p) or (ends.get(p, d), "black"))
    monkeypatch.setattr(end_finder, "store", lambda f, p, e, d: stored.__setitem__(p, (e, d)))
    monkeypatch.setattr(end_finder, "set_length",
                        lambda es, end: [lengths.__setitem__(e.dbid, end) for e in es])
    monkeypatch.setattr(end_finder, "upcoming", lambda st: soon or {})
    return looked, lengths, stored


def entry(path, dbid, kind="feature", duration=1500.0):
    return NS(path=path, realpath=path, dbid=dbid, content_type=kind, media_type="video", duration=duration)


def station(name):
    return {"network_name": name, "_has_catalog": True}


def test_what_plays_soonest_is_looked_at_first_and_its_channels_planned_again(monkeypatch):
    a, b = station("A"), station("B")
    entries = {"A": [entry("/x", 1), entry("/y", 2), entry("/bump", 3, "bump")],
               "B": [entry("/y", 4), entry("/ad", 5, "commercial")]}
    at = datetime.datetime(2026, 11, 22, 20)
    soon = {"/y": (at, {"A", "B"}), "/x": (at + datetime.timedelta(hours=1), {"A"})}
    looked, lengths, stored = _finder(monkeypatch, [a, b], entries, {}, {"/y": 1490.0, "/ad": 1400.0}, soon)
    assert end_finder.find_ends() == ([a, b], False)
    assert looked == ["/y", "/x", "/ad"]  # not the bumper
    assert lengths == {2: 1490.0, 4: 1490.0, 5: 1400.0}
    assert stored["/x"] == (1500.0, 1500.0)  # looked at: nothing to cut


def test_ends_found_go_into_catalogs_built_without_them(monkeypatch):
    a = station("A")
    entries = {"A": [entry("/x", 1), entry("/y", 2, duration=1490.0), entry("/z", 3, duration=900.0)]}
    found = {"/x": (1490.0, 1500.0), "/y": (1490.0, 1500.0), "/z": (1490.0, 1500.0)}
    looked, lengths, stored = _finder(monkeypatch, [a], entries, found, {})
    assert end_finder.find_ends() == ([], False)
    assert looked == ["/z"]  # another file under that name now
    assert lengths == {1: 1490.0}  # /y has it already


def test_an_hour_of_looking_then_the_channels_are_planned(monkeypatch):
    a = station("A")
    entries = {"A": [entry(f"/e{i}", i) for i in range(3)]}
    soon = {f"/e{i}": (datetime.datetime(2026, 11, 22, 20 + i), {"A"}) for i in range(3)}
    looked, lengths, stored = _finder(monkeypatch, [a], entries, {}, {"/e0": 1495.0}, soon)
    monkeypatch.setattr(end_finder, "REBUILD_EVERY", 0)
    assert end_finder.find_ends() == ([a], True)
    assert looked == ["/e0"]


def test_a_tape_with_its_old_ads_cut_plays_that_much_shorter(monkeypatch):
    a = station("A")
    # /t was already set to where it ends, then ad_finder cut 300 s; /u is new; /v was cut for another file
    entries = {"A": [entry("/t", 1, duration=1490.0), entry("/u", 2), entry("/v", 3, duration=1490.0)]}
    found = {"/t": (1490.0, 1500.0), "/u": (1490.0, 1500.0), "/v": (1490.0, 1500.0)}
    cuts = {"/t": (1500.0, [(400.0, 500.0), (800.0, 1000.0)]), "/u": (1500.0, [(400.0, 500.0)]),
            "/v": (1800.0, [(400.0, 500.0)])}
    looked, lengths, stored = _finder(monkeypatch, [a], entries, found, {}, cuts=cuts)
    assert end_finder.find_ends() == ([], False)
    assert looked == []  # the ends found earlier stay
    assert lengths == {1: 1190.0, 2: 1390.0}
