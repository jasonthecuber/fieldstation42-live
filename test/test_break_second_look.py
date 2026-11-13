from types import SimpleNamespace as NS

from fs42_live import break_finder, break_listener, break_patterns
from fs42_live.break_patterns import learn, show_of

SHOW = "/mnt/storage/data/library/TV/Cheers (1982)"


def segments(points, duration=1500.0):
    edges = [0.0, *points, duration]
    return [{"chapter_start": edges[i], "chapter_end": edges[i + 1]} for i in range(len(edges) - 1)]


def cheers(n=6, middle=(680, 700, 710, 720, 740, 760)):
    return {f"{SHOW}/S01/e{i}.mp4": segments([60.0 + i, float(middle[i % len(middle)]), 1420.0 + i])
            for i in range(n)}


def test_the_show_is_the_folder_under_a_library_section_or_channel():
    assert show_of(f"{SHOW}/Cheers.COMPLETE/S01/e1.mp4") == SHOW
    assert show_of("/mnt/storage/data/channels/gameshows/Family Feud/1983.mp4") == \
        "/mnt/storage/data/channels/gameshows/Family Feud"
    assert show_of("/mnt/storage/data/channels/vintage/Feud Farewell.mp4") == \
        "/mnt/storage/data/channels/vintage/Feud Farewell.mp4"  # a lone file is its own show
    assert show_of("/somewhere/else/ep.mp4") == "/somewhere/else"


def test_a_show_learns_where_its_breaks_usually_fall():
    pattern = learn(cheers())[SHOW]
    windows = pattern.windows(1500.0)
    assert len(windows) == 3
    low, usual, high = windows[1]
    assert low < 680 and 715 <= usual <= 725 and high > 760
    assert windows[2][0] < 1420 < windows[2][2]  # the last one, counted from the end
    assert pattern.fits(1480) and not pattern.fits(2700)


def test_breaks_all_over_the_place_or_too_few_episodes_are_no_pattern():
    assert learn(cheers(middle=(300, 500, 700, 900, 1100, 1300))) == {}
    assert learn(cheers(n=4)) == {}
    unsure = set(list(cheers())[:2])
    assert learn(cheers(), unsure) == {}  # guesses don't teach


def test_a_closer_look_takes_what_it_sees_else_the_usual_time(monkeypatch):
    pattern = learn(cheers())[SHOW]
    seen = iter([65.0, None, 1418.0])
    monkeypatch.setattr(break_patterns, "_best_in", lambda path, low, high, usual: next(seen))
    points, how = break_patterns.look_closer("x.mp4", 1500.0, pattern)
    assert how == "closer"
    assert points[0] == 65.0 and 715 <= points[1] <= 725 and points[2] == 1418.0
    monkeypatch.setattr(break_patterns, "_best_in", lambda *a: None)
    assert break_patterns.look_closer("x.mp4", 1500.0, pattern)[1] == "usual"


def test_the_host_saying_theyll_be_right_back_is_a_cue():
    word = lambda w, end: NS(word=w, end=end)
    said = [NS(text=" Survey says!", end=10.0, words=[word(" Survey", 9.0), word(" says!", 10.0)]),
            NS(text=" We'll be back right after this. Stay with", end=400.0,
               words=[word(" We'll", 390.0), word(" be", 390.4), word(" back", 390.8), word(" right", 391.2),
                      word(" after", 391.6), word(" this.", 392.0), word(" Stay", 399.0), word(" with", 400.0)]),
            NS(text=" Right after the message.", end=1040.0, words=[])]
    assert break_listener.cues(said) == [(392.0, "We'll be back right after this. Stay with"),
                                         (1040.0, "Right after the message.")]


def _episodes(monkeypatch, stored, looked, files, cues=None):
    st = {"network_name": "Shows", "_has_catalog": True, "_has_schedule": True}
    entries = [NS(path=p, realpath=p, dbid=1, content_type="feature", duration=1500.0, title=p) for p in files]
    scanned, listened = [], []
    monkeypatch.setattr(break_finder, "StationManager", lambda: NS(stations=[st]))
    monkeypatch.setattr(break_finder, "CatalogAPI", NS(get_entries=lambda s: entries))
    monkeypatch.setattr(break_finder, "FluidBuilder", lambda: None)
    monkeypatch.setattr(break_finder, "follow_moves", lambda s: 0)
    monkeypatch.setattr(break_finder, "_stored", lambda f: dict(stored))
    monkeypatch.setattr(break_finder, "_looked", lambda: dict(looked))
    monkeypatch.setattr(break_finder, "_save_looked", lambda new: looked.update(new))
    monkeypatch.setattr(break_finder, "find_breaks", lambda p, d: scanned.append(p) or [])
    monkeypatch.setattr(break_finder, "store", lambda f, p, s: stored.__setitem__(p, s))
    monkeypatch.setattr(break_finder, "upcoming", lambda s: {})
    monkeypatch.setattr(break_finder, "_pending", lambda: set())
    monkeypatch.setattr(break_finder, "_save_pending", lambda names: None)
    monkeypatch.setattr(break_finder, "peer_breaks", lambda: {})
    monkeypatch.setattr(break_patterns, "_best_in", lambda *a: None)
    monkeypatch.setattr(break_listener, "available", lambda: cues is not None)
    monkeypatch.setattr(break_listener, "listen", lambda p: listened.append(p) or (cues(p) if cues else None))
    return scanned, listened


def test_an_episode_with_nothing_clear_takes_its_shows_usual_breaks(monkeypatch):
    stored, looked = cheers(), {}
    new, old = f"{SHOW}/S02/new.mp4", f"{SHOW}/S02/old.mp4"
    stored[old] = []  # scanned before there were second looks
    scanned, listened = _episodes(monkeypatch, stored, looked, [new, old])

    break_finder.scan_new_episodes()
    assert scanned == [new]  # the old one isn't scanned again, just looked at
    assert looked == {new: "usual", old: "usual"}
    assert len(stored[new]) == 4 and 715 <= stored[new][1]["chapter_end"] <= 725


def test_a_game_show_is_listened_to_until_it_never_says_so(monkeypatch):
    feud = "/mnt/storage/data/channels/gameshows/Family Feud"
    files = [f"{feud}/{i}.mp4" for i in range(2)]
    stored, looked = {}, {}
    scanned, listened = _episodes(monkeypatch, stored, looked, files,
                                  cues=lambda p: [(391.0, "We'll be back right after this.")])
    break_finder.scan_new_episodes()
    assert looked == {files[0]: "heard", files[1]: "heard"}
    assert stored[files[0]][0]["chapter_end"] == 391.0

    sitcom = "/mnt/storage/data/channels/lost80s/Gimme a Break"
    files = [f"{sitcom}/{i}.mp4" for i in range(5)]
    stored, looked = {}, {}
    scanned, listened = _episodes(monkeypatch, stored, looked, files, cues=lambda p: [])
    break_finder.scan_new_episodes()
    assert listened == files[:3]
    assert [looked[f] for f in files] == ["quiet"] * 3 + ["none"] * 2


def test_an_episode_that_cant_be_listened_to_yet_waits(monkeypatch):
    feud = "/mnt/storage/data/channels/gameshows/Family Feud"
    files = [f"{feud}/{i}.mp4" for i in range(2)]
    stored, looked = {}, {}
    scanned, listened = _episodes(monkeypatch, stored, looked, files, cues=lambda p: None)
    assert break_finder.scan_new_episodes()[1] is False
    assert listened == [files[0]]  # once; the rest wait too
    assert looked == {} and stored == {files[0]: [], files[1]: []}
