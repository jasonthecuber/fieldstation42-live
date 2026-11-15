"""Control Room's "Smudge closer to the logo": the box without the room left for a glow (#94)."""

from fs42_live import logos


def test_close_takes_off_the_room_around_the_outline():
    # Family Feud's badge: found as a 73 x 71 outline, stored with 20% and 3 px each side
    assert logos.close([367, 254, 112, 105]) == [383, 269, 80, 75]


def test_logo_filter_close(tmp_path, monkeypatch):
    video = tmp_path / "Show.mp4"
    video.write_bytes(b"x" * 10)
    path = str(video.resolve())
    monkeypatch.setattr(logos.settings, "get", lambda key: True)
    monkeypatch.setattr(logos, "_known", {path: {"size": 10, "boxes": [[367, 254, 112, 105]], "harder": False}})
    monkeypatch.setattr(logos, "_choices", {})
    assert logos.logo_filter(path) == "delogo=x=367:y=254:w=112:h=105,"
    monkeypatch.setattr(logos, "_choices", {str(tmp_path.resolve()): "close"})
    assert logos.logo_filter(path) == "delogo=x=383:y=269:w=80:h=75,"
    monkeypatch.setattr(logos, "_choices", {path: "show"})
    assert logos.logo_filter(path) == ""
