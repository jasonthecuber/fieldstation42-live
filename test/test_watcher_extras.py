"""fs42_live: the catalog watcher sees a show's extras as not there, as the catalog does (#98), and the
web player's notes go to runtime/player.log too (#91)."""
import os

from fs42_live import catalog_watcher


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")


def test_media_in_skips_extras(tmp_path):
    top = str(tmp_path)
    _touch(f"{top}/Newhart/Season 1/S01E01.mkv")
    _touch(f"{top}/Newhart/Extras/Gag Reel.mkv")
    _touch(f"{top}/Movie (1990)/Movie (1990)-trailer.mkv")
    _touch(f"{top}/Movie (1990)/Movie (1990).mkv")
    _touch(f"{top}/commercial/Some Film-trailer.mp4")
    _touch(f"{top}/bump/Extras/up next.mp4")
    station = {"content_dir": top, "commercial_dir": "commercial",
               "monday": {"8": {"tags": "Newhart", "bump_dir": {"tags": "bump", "chance": 0.5}}}}
    names = {os.path.relpath(e.split("|")[0], top) for e in catalog_watcher.media_in(top, station)}
    assert names == {os.path.join("Newhart", "Season 1", "S01E01.mkv"), os.path.join("Movie (1990)", "Movie (1990).mkv"),
                     os.path.join("commercial", "Some Film-trailer.mp4"), os.path.join("bump", "Extras", "up next.mp4")}
    assert len(catalog_watcher.media_in(top)) == 6  # without the station, everything as before


def test_folder_of_only_extras_is_not_emptied(tmp_path):
    top = str(tmp_path)
    _touch(f"{top}/Show/Featurettes/a.mkv")
    os.makedirs(f"{top}/Gone")
    before = {f"{top}/Show/Featurettes/a.mkv|1", f"{top}/Gone/b.mkv|1"}
    assert catalog_watcher.folders_emptied(top, before, set()) == ["Gone"]


def test_player_log_file(tmp_path, monkeypatch):
    from fs42_live import router
    monkeypatch.setattr(router, "PLAYER_LOG", str(tmp_path / "player.log"))
    log = router._player_log()
    try:
        log.info("player 1.2.3.4: stall")
        for h in log.handlers:
            h.flush()
        assert "player 1.2.3.4: stall" in (tmp_path / "player.log").read_text()
    finally:
        for h in list(log.handlers):
            log.removeHandler(h)
            h.close()
