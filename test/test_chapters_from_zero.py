"""
Tests for chapter lists that start before 0 (#114): Family Ties' files mark chapters from -160.7s in
a video that starts at 0, so the Sitcoms channel started the episode from -160.7s, a frozen picture
until the first break. Shifted forward they meet the black frames the box finds between the acts.

Running
-------
    python3 -m unittest test.test_chapters_from_zero -v
"""

import sys
import unittest
from unittest.mock import MagicMock

sys.modules.setdefault("ffmpeg", MagicMock())
sys.modules.setdefault("moviepy", MagicMock())
sys.modules.setdefault("moviepy.editor", MagicMock())

from fs42.media_processor import MediaProcessor  # noqa: E402

FAMILY_TIES = [  # S04E10 as ffprobe gives it
    {"chapter_start": -160.693867, "chapter_end": 0.0, "title": "Chapter 1"},
    {"chapter_start": 0.0, "chapter_end": 550.7502, "title": "Chapter 2"},
    {"chapter_start": 550.7502, "chapter_end": 1247.212633, "title": "Chapter 3"},
    {"chapter_start": 1247.212633, "chapter_end": 1439.805033, "title": "Chapter 4"},
]


class ChaptersFromZero(unittest.TestCase):
    def test_a_list_starting_before_0_is_shifted_to_start_at_0(self):
        out = MediaProcessor.chapters_from_zero([dict(c) for c in FAMILY_TIES])
        self.assertEqual([round(c["chapter_start"], 1) for c in out], [0.0, 160.7, 711.4, 1407.9])
        self.assertEqual(round(out[-1]["chapter_end"], 1), 1439.8)  # still ends at the file's end
        self.assertEqual([c["title"] for c in out], ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"])
        for c in out:
            self.assertAlmostEqual(c["segment_duration"], c["chapter_end"] - c["chapter_start"])

    def test_it_lands_on_the_black_frames_between_the_acts(self):
        black = [160.088, 710.7765, 1407.185]  # what black detection found in the same file
        out = MediaProcessor.chapters_from_zero([dict(c) for c in FAMILY_TIES])
        for start, gap in zip([c["chapter_start"] for c in out[1:]], black):
            self.assertLess(abs(start - gap), 1.0)

    def test_a_list_starting_at_0_is_left_alone(self):
        chapters = [{"chapter_start": 0.0, "chapter_end": 600.0}, {"chapter_start": 600.0, "chapter_end": 1300.0}]
        self.assertIs(MediaProcessor.chapters_from_zero(chapters), chapters)
        self.assertEqual(MediaProcessor.chapters_from_zero([]), [])
        self.assertIsNone(MediaProcessor.chapters_from_zero(None))

    def test_a_chapter_pushed_past_the_end_is_dropped(self):
        chapters = [{"chapter_start": -100.0, "chapter_end": 0.0}, {"chapter_start": 0.0, "chapter_end": 1300.0},
                    {"chapter_start": 1300.0, "chapter_end": 1350.0}]
        out = MediaProcessor.chapters_from_zero(chapters)
        self.assertEqual([(c["chapter_start"], c["chapter_end"]) for c in out], [(0.0, 100.0), (100.0, 1350.0)])


if __name__ == "__main__":
    unittest.main()
