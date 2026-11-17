"""fs42_live (#102): Up next pops up 10 and 2 minutes before a show ends, only over the show."""
import datetime
import os
import sys
from types import SimpleNamespace as NS

sys.path.append(os.getcwd())
from fs42_live import up_next

START = datetime.datetime(2026, 11, 16, 20, 0)


def entry(seconds, kind="feature"):
    return NS(duration=seconds, content_type=kind)


def mins(times):
    return [round((t - START).total_seconds() / 60, 2) for t in times]


def test_ten_and_two_minutes_before_the_show_ends():
    # a 22-minute show in three parts, breaks between, a bumper after
    plan = [entry(600), entry(120, "commercial"), entry(420), entry(120, "commercial"), entry(300),
            entry(240, "commercial"), entry(30, "bump")]
    # the show ends at 26:00; 16:00 falls in its second part, 24:00 in its last
    assert mins(up_next.popups(plan, START, 8.8)) == [16.0, 24.0]


def test_one_falling_in_a_break_moves_to_the_nearest_part_of_the_show():
    # the show ends at 30:00; 20:00 is in the break 18:00-21:00, nearer its end
    plan = [entry(1080), entry(180, "commercial"), entry(540)]
    assert mins(up_next.popups(plan, START, 8.8)) == [round(21 + 1 / 60, 2), 28.0]


def test_never_runs_into_a_break():
    # 10 minutes before the end is 3 seconds before a break
    plan = [entry(603), entry(120, "commercial"), entry(600)]
    first = (up_next.popups(plan, START, 8.8)[0] - START).total_seconds()
    assert first + 8.8 <= 603 - up_next.EDGE or first >= 723 + up_next.EDGE


def test_a_short_show_gets_one():
    # a 5-minute cartoon: 10 minutes before its end is before it starts
    plan = [entry(300), entry(60, "bump")]
    assert mins(up_next.popups(plan, START, 8.8)) == [3.0]


def test_no_show_no_popup():
    assert up_next.popups([entry(120, "commercial")], START, 8.8) == []


class Manager:
    def __init__(self, blocks):
        self.blocks = blocks

    def get_programming_block(self, network, when):
        for b in self.blocks:
            if b.start_time <= when <= b.end_time:
                return b
        raise Exception("out of schedule")


def blocks():
    plan = [entry(600), entry(120, "commercial"), entry(420), entry(120, "commercial"), entry(540)]
    this = NS(title="Family Feud", start_time=START, end_time=START + datetime.timedelta(minutes=30),
              plan=plan, content=None)
    nxt = NS(title="The Price Is Right", start_time=this.end_time,
             end_time=this.end_time + datetime.timedelta(minutes=30), plan=plan, content=None)
    return [this, nxt]


def test_for_segment_names_the_next_show_where_it_pops_up():
    station = {"up_next": True}
    # the show ends at 30:00; 20:00 is in the break 19:00-21:00, so that one is just after it, and 28:00
    when = START + datetime.timedelta(minutes=21)
    assert up_next.for_segment(station, "n", when, 540, Manager(blocks())) == [
        (1.0, "The Price Is Right", None), (420.0, "The Price Is Right", None)]
    when = START + datetime.timedelta(minutes=12)
    assert up_next.for_segment(station, "n", when, 420, Manager(blocks())) == []


def test_off_unless_the_channel_has_it():
    when = START + datetime.timedelta(minutes=19)
    assert up_next.for_segment({}, "n", when, 540, Manager(blocks())) == []


def test_nothing_when_the_schedule_ends():
    when = START + datetime.timedelta(minutes=19)
    assert up_next.for_segment({"up_next": True}, "n", when, 540, Manager(blocks()[:1])) == []


def test_the_filter_is_one_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(up_next, "TEXTS", str(tmp_path))
    monkeypatch.setattr(up_next, "ART_DIR", str(tmp_path / "none"))
    assert up_next.video_filter([]) is None
    plain = up_next.video_filter([(30.0, "Cheers")])
    assert ";" not in plain and "drawbox" in plain
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "art.mov").write_bytes(b"")
    monkeypatch.setattr(up_next, "ART_DIR", str(tmp_path / "art"))
    two = up_next.video_filter([(30.0, "Cheers"), (-2.0, "Cheers")])
    assert two.count("overlay=") == 2 and "trim=start=2.000" in two
    assert not two.startswith("[") and not two.rstrip().endswith("]")


def test_a_long_title_is_smaller_to_fit():
    assert round(up_next.ems("Cheers"), 3) == 3.39
    assert up_next.ems("Battle of the Network Stars") * 28 < 364


def test_a_short_last_part_still_gets_the_two_minute_one():
    # the last part is 12 seconds of credits after the last break: that one comes before the break
    plan = [entry(215), entry(180, "commercial"), entry(1200), entry(180, "commercial"), entry(12)]
    assert mins(up_next.popups(plan, START, 8.8)) == [round(1187 / 60, 2), round((1595 - 1 - 8.8) / 60, 2)]


def part(seconds, path, skip=0.0, kind="feature"):
    return NS(duration=seconds, content_type=kind, path=path, skip=skip, is_stream=False)


def test_the_preview_comes_from_the_middle_of_the_next_show():
    # one episode in three parts (skip into the file), breaks and a still between
    plan = [part(600, "ep.mkv"), part(120, "ad.mp4", kind="commercial"), part(420, "ep.mkv", 600),
            part(30, "card.png", kind="commercial"), part(300, "ep.mkv", 1020)]
    seen = set()
    for seed in range(40):
        path, at = up_next.preview_spot(plan, seed, 5.8)
        assert path == os.path.abspath("ep.mkv")
        assert 0.3 * 1320 - 6 <= at <= 0.7 * 1320 + 6
        seen.add(round(at))
    assert len(seen) > 20  # a different spot each time
    assert up_next.preview_spot(plan, "a", 5.8) == up_next.preview_spot(plan, "a", 5.8)


def test_no_preview_from_stills_or_streams():
    assert up_next.preview_spot([part(600, "card.png")], 1, 5.8) is None
    assert up_next.preview_spot([NS(duration=600, content_type="feature", path="http://x", is_stream=True)],
                                1, 5.8) is None
    assert up_next.preview_spot([part(5, "ep.mkv")], 1, 5.8) is None


def test_the_preview_goes_behind_the_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(up_next, "TEXTS", str(tmp_path))
    (tmp_path / "art.mov").write_bytes(b"")
    monkeypatch.setattr(up_next, "ART_DIR", str(tmp_path))
    f = up_next.video_filter([(30.0, "Cheers", ("/m/ep.mkv", 700.0)), (-3.0, "Cheers", ("/m/ep.mkv", 900.0))])
    assert f.count("overlay=") == 4
    assert f.index("seek_point=700.000") < f.index("art.mov")
    assert "seek_point=901.500" in f and "trim=duration=4.300" in f  # started 1.5s into it
    assert not f.startswith("[") and not f.rstrip().endswith("]")
    (tmp_path / "art.json").write_text('{"preview": false}')
    assert "seek_point" not in up_next.video_filter([(30.0, "Cheers", ("/m/ep.mkv", 700.0))])


def test_paths_with_brackets_and_quotes_stay_one_value():
    # #103: a show folder named "... TVRip x264 [i_c]" cut the movie filter's path at the bracket
    path = "/media/Show 1960 [i_c]; it's: a, b/ep.mkv"
    esc = up_next._esc(path)
    for raw in "[]';,":
        assert all(esc[i - 1] == "\\" for i, c in enumerate(esc) if c == raw)
    assert "\[i_c\]" in esc and "\\\:" in esc
