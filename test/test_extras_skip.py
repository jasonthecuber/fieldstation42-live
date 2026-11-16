"""fs42_live (#98): a show's or movie's extras stay off its channel."""
import os
import sys

sys.path.append(os.getcwd())
from fs42.media_processor import MediaProcessor


def test_extras_folders_and_endings():
    top = "/c/sci_fi/Show"
    assert MediaProcessor.is_extra(f"{top}/Season 1/Featurettes/Gag Reel.mkv", top)
    assert MediaProcessor.is_extra(f"{top}/Extras/Interview.mkv", top)
    assert MediaProcessor.is_extra(f"{top}/Behind The Scenes/a.mp4", top)
    assert MediaProcessor.is_extra(f"{top}/Movie (2019)-trailer.mkv", top)
    assert MediaProcessor.is_extra(f"{top}/Movie (2019)-behindthescenes.mkv", top)


def test_episodes_stay():
    top = "/c/classic_sitcoms/Three's Company"
    assert not MediaProcessor.is_extra(f"{top}/Season 07/Three's Company S07E04 (Extra, Extra).mp4", top)
    assert not MediaProcessor.is_extra(f"{top}/Season 1/Show - S01E02 - The Sample.mp4", top)
    assert not MediaProcessor.is_extra(f"{top}/Specials/Show S00E00 Pilot.mp4", top)
    # the tag folder's own name (or what's above it) doesn't count
    assert not MediaProcessor.is_extra("/c/Extras/Show/S01E01.mkv", "/c/Extras/Show")


def test_process_subs_skips_extras(tmp_path, monkeypatch):
    for rel in ("Season 1/S01E01.mkv", "Season 1/Featurettes/Making Of.mkv", "Extras/Gag Reel.mkv"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    monkeypatch.setattr(MediaProcessor, "_process_media",
                        staticmethod(lambda files, tag, **kw: [os.path.basename(f) for f in files]))
    got = MediaProcessor._process_subs(str(tmp_path), "Show", skip_extras=True)
    assert got == ["S01E01.mkv"]
    everything = MediaProcessor._process_subs(str(tmp_path), "Show")
    assert sorted(everything) == ["Gag Reel.mkv", "Making Of.mkv", "S01E01.mkv"]
