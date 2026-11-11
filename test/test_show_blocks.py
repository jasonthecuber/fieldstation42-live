"""
Tests for live_show_blocks: a commercial-free channel plays a show (a folder under its tag) at a time,
for about a block of the clock, with one bumper as each show's block starts (#74).

Running
-------
    python3 -m unittest test.test_show_blocks -v
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
from fs42.liquid_blocks import LiquidBlock  # noqa: E402
from fs42.liquid_schedule import LiquidSchedule  # noqa: E402
from fs42_live.guide import in_blocks  # noqa: E402

START = datetime.datetime(2026, 11, 11, 10, 0)
SLOT = {"tags": "life"}


def _entry(path, duration, tag="life"):
    return SimpleNamespace(path=path, realpath=path, duration=duration, title="Clip", content_type="feature",
                           media_type="video", hints=[], tag=tag, count=0)


def _schedule(shows, blocks=30):
    conf = {
        "network_name": "Lifestyle", "content_dir": "catalog/lifestyle", "commercial_free": True,
        "schedule_increment": 0, "break_strategy": "standard", "break_duration": 120, "bump_dir": "bump",
        "clip_shows": {}, "live_show_blocks": blocks,
    }
    catalog = ShowCatalog.__new__(ShowCatalog)
    catalog.config = conf
    catalog.clip_index = {
        "life": [_entry(f"catalog/lifestyle/life/{show}/{i}.mp4", length)
                 for show, (count, length) in shows.items() for i in range(count)],
        "bump": [_entry("catalog/lifestyle/bump/ident.mp4", 10, "bump")],
    }
    catalog._l = logging.getLogger("test")
    sched = LiquidSchedule.__new__(LiquidSchedule)
    sched.conf = conf
    sched.catalog = catalog
    sched._blocks = []
    sched._l = logging.getLogger("test")
    return sched


def _run(sched, until):
    mark, out = START, []
    while mark < until:
        block, mark = sched._fill(SLOT, "life", mark)
        out.append(block)
    return out


def _show(block):
    return block.content.path.split("/")[3]


class TestShowBlocks(unittest.TestCase):
    def test_a_show_at_a_time_with_one_bumper_each(self):
        sched = _schedule({"Ida": (200, 50), "Valia": (200, 45)})
        blocks = _run(sched, START + datetime.timedelta(hours=3))
        runs = []
        for b in blocks:
            if not runs or runs[-1][0] != _show(b):
                runs.append([_show(b), b.start_time, b.end_time, 0])
            runs[-1][2] = b.end_time
            runs[-1][3] += bool(b.start_bump)
        self.assertEqual([r[0] for r in runs[:4]], ["Ida", "Valia", "Ida", "Valia"])
        for show, start, end, bumps in runs[:-1]:
            self.assertEqual(bumps, 1)  # only the block's first video has one
            self.assertTrue(datetime.timedelta(minutes=25) < end - start < datetime.timedelta(minutes=32), (show, start, end))
        self.assertTrue(all(b.end_bump is None for b in blocks))

    def test_a_long_video_runs_over_and_the_next_show_follows(self):
        sched = _schedule({"Ida": (3, 45 * 60), "Valia": (100, 50)})
        blocks = _run(sched, START + datetime.timedelta(hours=1))
        self.assertEqual(_show(blocks[0]), "Ida")
        self.assertEqual(_show(blocks[1]), "Valia")  # 10:45, past Ida's block
        self.assertTrue(blocks[1].start_bump)

    def test_the_show_played_longest_ago_goes_next(self):
        sched = _schedule({"A": (50, 60), "B": (50, 60), "C": (50, 60)})
        sched._blocks = [LiquidBlock(_entry("catalog/lifestyle/life/A/1.mp4", 60),
                                     START - datetime.timedelta(minutes=5), START, "A")]
        blocks = _run(sched, START + datetime.timedelta(hours=1, minutes=30))
        order = []
        for b in blocks:
            if not order or order[-1] != _show(b):
                order.append(_show(b))
        self.assertEqual(order[:3], ["B", "C", "A"])

    def test_guide_lists_a_show_once_per_block(self):
        station = {"live_show_blocks": 30, "content_dir": "catalog/lifestyle"}
        blocks = _run(_schedule({"Ida - Cleaning & Home": (200, 50), "Valia": (200, 45)}),
                      START + datetime.timedelta(hours=1))
        listed = in_blocks(station, blocks)
        self.assertEqual([x.title for x in listed][:2], ["Ida: Cleaning & Home", "Valia"])
        self.assertEqual(listed[0].start_time, START)
        self.assertEqual(listed[1].start_time, listed[0].end_time)


if __name__ == "__main__":
    unittest.main()
