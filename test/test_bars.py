"""
Tests for bars.find: the picture inside black bars made part of a video, from cropdetect's samples.

Running
-------
    python3 -m unittest test.test_bars -v
"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules.setdefault("ffmpeg", MagicMock())
sys.modules.setdefault("moviepy", MagicMock())
sys.modules.setdefault("moviepy.editor", MagicMock())

from fs42_live import bars  # noqa: E402


def _find(crop, size="1920x1080 [SAR 1:1 DAR 16:9]"):
    """bars.find on a file whose every sample cropdetect finds as `crop` (w:h:x:y)."""
    log = f"Stream #0:0: Video: h264 (High), yuv420p(tv, bt709), {size}, 29.97 fps\ncrop={crop}\n"
    with patch.object(bars.subprocess, "run", return_value=SimpleNamespace(stderr=log)):
        return bars.find("/x.mp4", 3600)


class TestFind(unittest.TestCase):
    def test_pillarbox(self):
        self.assertEqual(_find("1440:1080:240:0"), [1440, 1080, 240, 0])

    def test_logo_on_one_bar(self):
        # Sesame Street's uploads: a logo on the right bar takes the picture found over to it
        self.assertEqual(_find("1624:1080:234:0"), [1452, 1080, 234, 0])

    def test_logo_on_the_left_bar(self):
        self.assertEqual(_find("1624:1080:62:0"), [1452, 1080, 234, 0])

    def test_letterbox(self):
        self.assertEqual(_find("640:360:0:60", "640x480 [SAR 1:1 DAR 4:3]"), [640, 360, 0, 60])

    def test_none(self):
        self.assertIsNone(_find("1920:1080:0:0"))

    def test_uneven_not_4_3_left_alone(self):
        # a dark scene down one side isn't bars
        self.assertIsNone(_find("1500:1080:400:0"))

    def test_anamorphic(self):
        # a DVD's 720x480 at SAR 32:27 is 16:9; its 4:3 picture is 540 pixels wide, a logo on the right bar
        self.assertEqual(_find("600:480:90:0", "720x480 [SAR 32:27 DAR 16:9]"), [540, 480, 90, 0])


if __name__ == "__main__":
    unittest.main()
