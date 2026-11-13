import datetime
import json
import os
import sqlite3
from types import SimpleNamespace as NS

from fs42_live import break_finder, catalog_watcher


def episode(path, realpath=None, dbid=1):
    return NS(path=path, realpath=realpath, dbid=dbid, content_type="feature", duration=1500, title=path)


def _scanner(monkeypatch, stations, entries, soon=None, shared=None):
    scanned, stored, pending = [], {}, set()
    monkeypatch.setattr(break_finder, "StationManager", lambda: NS(stations=stations))
    monkeypatch.setattr(break_finder, "CatalogAPI", NS(get_entries=lambda s: entries[s["network_name"]]))
    monkeypatch.setattr(break_finder, "FluidBuilder", lambda: None)
    monkeypatch.setattr(break_finder, "follow_moves", lambda s: 0)
    monkeypatch.setattr(break_finder, "is_scanned", lambda f, p: p in stored)
    monkeypatch.setattr(break_finder, "find_breaks", lambda p, d: scanned.append(p) or [300.0, 700.0, 1100.0])
    monkeypatch.setattr(break_finder, "store", lambda f, p, s: stored.__setitem__(p, s))
    monkeypatch.setattr(break_finder, "upcoming", lambda st: soon or {})
    monkeypatch.setattr(break_finder, "_pending", lambda: set(pending))
    monkeypatch.setattr(break_finder, "_save_pending", lambda names: pending.update(names))
    if shared is None:
        monkeypatch.setattr(break_finder, "peer_breaks", lambda: {})
    return scanned, stored, pending


def station(name):
    return {"network_name": name, "_has_catalog": True, "_has_schedule": True}


def test_what_plays_soonest_goes_first_then_channels_with_fewest_left(monkeypatch):
    big, small = station("Big"), station("Small")
    entries = {"Big": [episode(f"/b{i}") for i in range(4)], "Small": [episode("/s0")]}
    at = datetime.datetime(2026, 11, 13, 20)
    soon = {"/b2": (at, {"Big"}), "/b1": (at + datetime.timedelta(hours=1), {"Big", "Small"})}
    scanned, stored, pending = _scanner(monkeypatch, [big, small], entries, soon)

    # each episode about to air hands its schedules back to be rebuilt right away
    assert break_finder.scan_new_episodes() == ([big], True)
    assert scanned == ["/b2"]
    assert break_finder.scan_new_episodes() == ([big, small], True)
    assert scanned == ["/b2", "/b1"]
    assert break_finder.scan_new_episodes() == ([big, small], False)  # the rest aren't on a schedule
    assert scanned == ["/b2", "/b1", "/s0", "/b0", "/b3"]


def test_episodes_not_on_a_schedule_go_on_for_an_hour(monkeypatch):
    big = station("Big")
    entries = {"Big": [episode(f"/b{i}") for i in range(3)]}
    scanned, stored, pending = _scanner(monkeypatch, [big], entries)
    monkeypatch.setattr(break_finder, "REBUILD_EVERY", 0)

    assert break_finder.scan_new_episodes() == ([], True)  # one scanned, then back
    assert scanned == ["/b0"]
    assert break_finder.scan_new_episodes() == ([], True)
    assert break_finder.scan_new_episodes() == ([], False)
    assert scanned == ["/b0", "/b1", "/b2"]


def test_the_same_file_scanned_on_another_box_takes_its_breaks(tmp_path, monkeypatch):
    same, other = tmp_path / "Show S01E01.mkv", tmp_path / "Show S01E02.mkv"
    same.write_bytes(b"x" * 10)
    other.write_bytes(b"x" * 10)
    peers = tmp_path / "peer_breaks"
    peers.mkdir()
    theirs = [{"chapter_start": 0.0, "chapter_end": 300.0}]
    (peers / "crumpet.json").write_text(json.dumps({"files": [
        ["Show S01E01.mkv", 10, theirs], ["Show S01E02.mkv", 11, []]]}))  # E02's size differs: not the same file
    monkeypatch.setattr(break_finder, "PEERS", str(peers))
    shows = station("Shows")
    soon = {str(same): (datetime.datetime(2026, 11, 13, 20), {"Shows"})}
    scanned, stored, pending = _scanner(monkeypatch, [shows], {"Shows": [episode(str(same)), episode(str(other))]},
                                        soon, shared=True)

    assert break_finder.scan_new_episodes() == ([shows], True)  # rebuilt with them before scanning on
    assert stored[str(same)] == theirs
    assert scanned == []
    break_finder.scan_new_episodes()
    assert scanned == [str(other)]


def test_breaks_coming_in_from_another_box_are_taken_before_scanning_on(tmp_path, monkeypatch):
    peers = tmp_path / "peer_breaks"
    peers.mkdir()
    monkeypatch.setattr(break_finder, "PEERS", str(peers))
    shows = station("Shows")
    scanned, stored, pending = _scanner(monkeypatch, [shows], {"Shows": [episode(f"/e{i}") for i in range(3)]})
    arrived = lambda p, d: (scanned.append(p), (peers / "crumpet.json").write_text('{"files": []}'))[0] or []
    monkeypatch.setattr(break_finder, "find_breaks", arrived)

    assert break_finder.scan_new_episodes() == ([], True)
    assert scanned == ["/e0"]


def test_moved_folders_are_followed_with_their_breaks(tmp_path, monkeypatch):
    new = tmp_path / "channels" / "Show"
    new.mkdir(parents=True)
    (new / "ep.mp4").write_text("")
    link = tmp_path / "catalog" / "Show"
    link.parent.mkdir()
    os.symlink(new, link)
    db_path = tmp_path / "fluid.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE catalog_entries (id INTEGER PRIMARY KEY, realpath TEXT)")
        db.execute("INSERT INTO catalog_entries VALUES (1, '/old/Show/ep.mp4')")
        for table in ("break_points", "chapter_points"):
            db.execute(f"CREATE TABLE {table} (path TEXT PRIMARY KEY, points TEXT, last_updated TIMESTAMP)")
        db.execute("INSERT INTO break_points VALUES ('/old/Show/ep.mp4', '[1]', 0)")
    monkeypatch.setattr(catalog_watcher, "CatalogAPI",
                        NS(get_entries=lambda s: [episode(str(link / "ep.mp4"), "/old/Show/ep.mp4")]))
    monkeypatch.setattr(catalog_watcher, "CatalogIO", lambda: NS(_get_connection=lambda: sqlite3.connect(db_path)))

    assert catalog_watcher.follow_moves({"network_name": "Shows"}) == 1
    real = os.path.realpath(new / "ep.mp4")
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT realpath FROM catalog_entries").fetchone()[0] == real
        assert db.execute("SELECT points FROM break_points WHERE path = ?", (real,)).fetchone()[0] == "[1]"
