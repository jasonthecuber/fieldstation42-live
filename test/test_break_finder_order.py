import os
import sqlite3
from types import SimpleNamespace as NS

from fs42_live import break_finder, catalog_watcher


def episode(path, realpath=None, dbid=1):
    return NS(path=path, realpath=realpath, dbid=dbid, content_type="feature", duration=1500, title=path)


def test_stations_with_fewest_left_go_first_and_come_back_when_done(monkeypatch):
    big = {"network_name": "Big", "_has_catalog": True, "_has_schedule": True}
    small = {"network_name": "Small", "_has_catalog": True, "_has_schedule": True}
    entries = {"Big": [episode(f"/b{i}") for i in range(3)], "Small": [episode("/s0")]}
    scanned, pending = [], set()
    monkeypatch.setattr(break_finder, "StationManager", lambda: NS(stations=[big, small]))
    monkeypatch.setattr(break_finder, "CatalogAPI", NS(get_entries=lambda s: entries[s["network_name"]]))
    monkeypatch.setattr(break_finder, "FluidBuilder", lambda: None)
    monkeypatch.setattr(break_finder, "follow_moves", lambda s: 0)
    monkeypatch.setattr(break_finder, "is_scanned", lambda f, p: p in scanned)
    monkeypatch.setattr(break_finder, "find_breaks", lambda p, d: [300.0, 700.0, 1100.0])
    monkeypatch.setattr(break_finder, "store", lambda f, p, s: scanned.append(p))
    monkeypatch.setattr(break_finder, "plays_later", lambda s, p: True)
    monkeypatch.setattr(break_finder, "_pending", lambda: pending)
    monkeypatch.setattr(break_finder, "_save_pending", lambda names: None)

    assert break_finder.scan_new_episodes() == [small]
    assert scanned == ["/s0"]
    break_finder.rebuilt([small])  # what the schedule keeper does after rebuilding
    pending.discard("Small")
    assert break_finder.scan_new_episodes() == [big]
    assert scanned == ["/s0", "/b0", "/b1", "/b2"]


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
