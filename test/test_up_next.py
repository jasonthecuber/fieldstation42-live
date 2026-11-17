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
        (1.0, "The Price Is Right"), (420.0, "The Price Is Right")]
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
