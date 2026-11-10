"""
Tests for captions.wanted: which files get MTV-style captions.

Running
-------
    python3 -m unittest test.test_captions -v
"""

import unittest

from fs42_live import captions

MTV = {"content_dir": "catalog/music_videos", "live_captions": ["music_videos"]}


class TestWanted(unittest.TestCase):
    def test_music_video(self):
        self.assertTrue(captions.wanted(MTV, "catalog/music_videos/music_videos/a-ha - Take On Me (1985).mp4"))
        self.assertTrue(captions.wanted(MTV, "/home/media/fs42-data/catalog/music_videos/music_videos/x.mp4"))

    def test_bumpers_and_commercials(self):  # #72
        self.assertFalse(captions.wanted(MTV, "catalog/music_videos/bump/MTV ident.mp4"))
        self.assertFalse(captions.wanted(MTV, "catalog/music_videos/bump/music_videos/ident.mp4"))
        self.assertFalse(captions.wanted(MTV, "/home/media/fs42-data/catalog/music_videos/commercial/ad.mp4"))

    def test_off(self):
        self.assertFalse(captions.wanted({"content_dir": "catalog/music_videos"},
                                         "catalog/music_videos/music_videos/x.mp4"))


if __name__ == "__main__":
    unittest.main()
