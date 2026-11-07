"""
Tests for LiquidSchedule._bump_between: a commercial-free channel with no schedule
increment ends each show with one of its bumpers, since no gap is left to fill.

Running
-------
    python3 -m unittest test.test_bump_between -v
"""

import datetime
import logging
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules.setdefault("ffmpeg", MagicMock())
sys.modules.setdefault("moviepy", MagicMock())
sys.modules.setdefault("moviepy.editor", MagicMock())

from fs42.catalog import ShowCatalog  # noqa: E402
from fs42.liquid_schedule import LiquidSchedule  # noqa: E402

START = datetime.datetime(2026, 11, 7, 12, 0)


def _entry(path, duration):
    return SimpleNamespace(path=path, realpath=path, duration=duration, title="Clip", content_type="feature",
                           media_type="video", hints=[], tag="synth", count=0)


def _schedule(increment=0, free=True, bumps=True, **extra):
    conf = {
        "network_name": "PBS", "content_dir": "/content", "commercial_free": free,
        "schedule_increment": increment, "break_strategy": "standard", "break_duration": 120, "bump_dir": "bump",
        "clip_shows": {"synth": {"tags": "synth", "duration": 3600, "total_duration": 3600}}, **extra,
    }
    catalog = ShowCatalog.__new__(ShowCatalog)
    catalog.config = conf
    catalog.clip_index = {"bump": [_entry("/content/bump/ident.mp4", 12)]} if bumps else {}
    catalog._l = logging.getLogger("test")
    catalog.min_gap = 3
    catalog.gather_clip_content = MagicMock(return_value=[_entry("/content/synth/a.mp4", 1400)])
    sched = LiquidSchedule.__new__(LiquidSchedule)
    sched.conf = conf
    sched.catalog = catalog
    return sched


def _info():
    return {"start_bump": None, "end_bump": None, "bump_dir": None}


class TestBumpBetween(unittest.TestCase):
    def test_commercial_free_without_increment_ends_with_a_bump(self):
        info = _info()
        _schedule()._bump_between(info, 0, START)
        self.assertEqual(info["end_bump"], {"path": "/content/bump/ident.mp4", "duration": 12})

    def test_clip_show_runs_long_enough_for_its_bump(self):
        sched = _schedule()
        block, next_mark = sched._clip_fill("synth", START, {"tags": "synth"})
        self.assertEqual(next_mark, START + datetime.timedelta(seconds=1412))
        block.make_plan(sched.catalog)
        self.assertEqual(block.plan[-1].path, "/content/bump/ident.mp4")

    def test_a_channel_with_only_pre_and_post_bumps_uses_them(self):
        sched = _schedule(bumps=False)
        sched.catalog.clip_index = {"bump": [], "bump-prebump": [_entry("/content/bump/pre/a.mp4", 8)],
                                    "bump-postbump": [_entry("/content/bump/post/b.mp4", 9)]}
        seen = set()
        for _ in range(40):
            info = _info()
            sched._bump_between(info, 0, START)
            seen.add(info["end_bump"]["path"])
        self.assertEqual(seen, {"/content/bump/pre/a.mp4", "/content/bump/post/b.mp4"})

    def test_left_alone_otherwise(self):
        for sched, increment in ((_schedule(), 30), (_schedule(free=False), 0),
                                 (_schedule(bumps_between_shows=False), 0), (_schedule(bumps=False), 0)):
            info = _info()
            sched._bump_between(info, increment, START)
            self.assertIsNone(info["end_bump"])

    def test_keeps_a_configured_end_bump(self):
        info = dict(_info(), end_bump={"path": "/content/outro.mp4", "duration": 5})
        _schedule()._bump_between(info, 0, START)
        self.assertEqual(info["end_bump"]["path"], "/content/outro.mp4")


if __name__ == "__main__":
    unittest.main()
