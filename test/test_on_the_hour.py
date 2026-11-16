"""
Tests for on_the_hour (Features #98): on the half hour, a random slot plays one of its half-hour
shows, so hour shows start on the hour.

Running
-------
    python3 -m pytest test/test_on_the_hour.py
"""

import datetime
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules.setdefault("ffmpeg", MagicMock())
sys.modules.setdefault("moviepy", MagicMock())
sys.modules.setdefault("moviepy.editor", MagicMock())

from fs42.liquid_schedule import LiquidSchedule  # noqa: E402

HALF = datetime.datetime(2026, 11, 15, 17, 30)
LENGTHS = {"Knight Rider": [2880, 2900, 5400], "Cyberpunk": [1440, 1450], "Clips": [600]}


def _schedule():
    sched = LiquidSchedule.__new__(LiquidSchedule)
    sched.conf = {"network_name": "80s", "on_the_hour": True, "clip_shows": {"Clips": {}}}
    sched.catalog = SimpleNamespace(get_all_by_tag=lambda tag: [SimpleNamespace(duration=d) for d in LENGTHS.get(tag, [])] or None)
    return sched


SLOT = {"random_tags": True, "tags": ["Knight Rider", "Cyberpunk", "Clips"]}


def test_hour_show_on_the_half_hour_becomes_a_half_hour_show():
    assert _schedule()._half_hour_tag(SLOT, "Knight Rider", 0, HALF) == ("Cyberpunk", 1)


def test_half_hour_show_picked_stays():
    assert _schedule()._half_hour_tag(SLOT, "Cyberpunk", 1, HALF) == ("Cyberpunk", 1)


def test_slot_without_half_hour_shows_keeps_its_pick():
    slot = {"random_tags": True, "tags": ["Knight Rider", "Clips"]}
    assert _schedule()._half_hour_tag(slot, "Knight Rider", 0, HALF) == ("Knight Rider", 0)


def test_single_tag_slot_keeps_its_pick():
    assert _schedule()._half_hour_tag({"tags": "Knight Rider"}, "Knight Rider", None, HALF) == ("Knight Rider", None)
