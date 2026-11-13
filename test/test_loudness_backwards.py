"""Stereo with one channel wired backwards: found when measured, turned back over as it plays."""
import os
import re
import subprocess

import pytest

from fs42_live import loudness


def _make(path, pan):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=12:c=pink:a=0.3:r=48000",
                    "-af", pan, "-c:a", "aac", str(path)], check=True)
    return str(path)


def _mono_level(path, chain):
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                          chain + ",pan=mono|c0=0.5*c0+0.5*c1,volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", log).group(1))


@pytest.fixture
def known(monkeypatch):
    monkeypatch.setattr(loudness, "_known", {})
    return loudness._known


def test_a_backwards_stereo_file_is_found(tmp_path):
    assert loudness.backwards(_make(tmp_path / "b.m4a", "pan=stereo|c0=c0|c1=-1*c0"))


def test_ordinary_stereo_and_mono_are_left_alone(tmp_path):
    assert not loudness.backwards(_make(tmp_path / "s.m4a", "pan=stereo|c0=c0|c1=0.6*c0"))
    assert not loudness.backwards(_make(tmp_path / "m.m4a", "pan=mono|c0=c0"))


def test_a_silent_file_is_not_called_backwards(tmp_path):
    assert not loudness.backwards(_make(tmp_path / "q.m4a", "volume=0,pan=stereo|c0=c0|c1=-1*c0"))


def test_a_backwards_file_is_turned_back_over_as_it_plays(tmp_path, known):
    path = _make(tmp_path / "b.mp4", "pan=stereo|c0=c0|c1=-1*c0")
    known[path] = {"size": (tmp_path / "b.mp4").stat().st_size, "lufs": -24.0, "flip": True}
    chain = loudness.audio_filter(path)
    assert loudness.FLIP in chain
    # on one speaker: next to nothing as it is, and full sound once turned back over
    assert _mono_level(path, chain) > _mono_level(path, "anull") + 20


def test_files_measured_before_the_check_are_checked_once(tmp_path, known, monkeypatch):
    path = _make(tmp_path / "b.mp4", "pan=stereo|c0=c0|c1=-1*c0")
    known[path] = {"size": (tmp_path / "b.mp4").stat().st_size, "lufs": -24.0}
    monkeypatch.setattr(loudness, "StationManager", lambda: type("S", (), {"stations": []})())
    monkeypatch.setattr(loudness, "_save", lambda: None)
    monkeypatch.setattr(loudness, "LEVELLED", str(tmp_path / "levelled"))
    measured = []
    monkeypatch.setattr(loudness, "measure", lambda p: measured.append(p) or -24.0)
    loudness._queued.add(path)
    loudness.scan_new_files()
    assert known[path]["flip"] is True and measured == []  # not measured again, only checked
    assert loudness.FLIP in loudness.audio_filter(path)
    loudness.scan_new_files()
    assert measured == []


def test_what_viewers_meet_first_is_checked_first(tmp_path, known, monkeypatch):
    from fs42_live import break_finder
    import datetime
    files = {n: _make(tmp_path / f"{n}.mp4", "pan=stereo|c0=c0|c1=0.6*c0") for n in ("ad", "later", "next", "now", "met")}
    entry = lambda p, d: type("E", (), {"realpath": p, "path": p, "duration": d})()
    monkeypatch.setattr(loudness, "StationManager", lambda: type("S", (), {"stations": [{"_has_catalog": True}]})())
    monkeypatch.setattr(loudness.CatalogAPI, "get_entries",
                        lambda s: [entry(files["ad"], 30), entry(files["later"], 1800), entry(files["next"], 1800),
                                   entry(files["now"], 1800), entry(files["met"], 1800)])
    t = datetime.datetime.now()
    monkeypatch.setattr(break_finder, "upcoming", lambda stations, on_air=False: on_air and {
        files["now"]: (t - datetime.timedelta(minutes=10), {"A"}), files["next"]: (t + datetime.timedelta(minutes=20), {"A"})})
    monkeypatch.setattr(loudness, "_save", lambda: None)
    monkeypatch.setattr(loudness, "LEVELLED", str(tmp_path / "levelled"))
    measured = []
    monkeypatch.setattr(loudness, "measure", lambda p: measured.append(os.path.basename(p)) or -24.0)
    loudness._queued.add(files["met"])
    loudness.scan_new_files()
    assert measured == ["met.mp4", "now.mp4", "next.mp4", "ad.mp4", "later.mp4"]


def test_a_playing_file_measured_before_the_check_is_queued(tmp_path, known):
    path = _make(tmp_path / "b.mp4", "pan=stereo|c0=c0|c1=-1*c0")
    known[path] = {"size": (tmp_path / "b.mp4").stat().st_size, "lufs": -24.0}
    loudness._queued.discard(path)
    loudness.audio_filter(path)
    assert path in loudness._queued
