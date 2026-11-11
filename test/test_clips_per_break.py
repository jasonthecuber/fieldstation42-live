"""
Tests for clips_per_break: a clip show (MTV) plays that many clips between its breaks (#73).

Running
-------
    python3 -m unittest test.test_clips_per_break -v
"""

import unittest
from types import SimpleNamespace

from fs42.block_plan import BlockPlanEntry
from fs42.reel_cutter import ReelCutter


def clips(n):
    return [SimpleNamespace(path=f"v{i}", duration=240, content_type="clip", media_type="video") for i in range(n)]


def reels(n):
    return [SimpleNamespace(make_plan=lambda: [BlockPlanEntry("ad", 0, 120, content_type="commercial")])
            for _ in range(n)]


def layout(entries):
    return "".join("." if e.path == "ad" else "V" for e in entries)


class TestClipsPerBreak(unittest.TestCase):
    def test_four_then_a_break(self):
        plan = ReelCutter.cut_reels_into_clips(clips(11), reels(3), "standard", None, None, per_break=4)
        self.assertEqual(layout(plan), "VVVV.VVV.VVVV.")

    def test_groups_even_out(self):  # not 4, 4, 1
        plan = ReelCutter.cut_reels_into_clips(clips(9), reels(3), "standard", None, None, per_break=4)
        self.assertEqual(layout(plan), "VVV.VVV.VVV.")

    def test_even_groups_end_on_a_break(self):
        plan = ReelCutter.cut_reels_into_clips(clips(12), reels(3), "standard", None, None, per_break=4)
        self.assertEqual(layout(plan), "VVVV.VVVV.VVVV.")

    def test_unset_is_as_before(self):
        plan = ReelCutter.cut_reels_into_clips(clips(4), reels(4), "standard", None, None)
        self.assertEqual(layout(plan).count("."), 4)
        self.assertEqual(layout(plan)[0], "V")


if __name__ == "__main__":
    unittest.main()
