"""
Tests for live_title_from "file": a channel of one-off broadcasts titles each by its file name, but a
show in its own folder there is listed by the show, not "Mission Impossible S04E15 The Falcon" (#113).

Running
-------
    python3 -m unittest test.test_file_titles -v
"""

import unittest

from fs42_live.guide import show_title

STATION = {"content_dir": "catalog/vintage", "live_title_from": "file"}


class FileTitles(unittest.TestCase):
    def test_a_one_off_broadcast_is_titled_by_its_file_name(self):
        path = "catalog/vintage/vintage/ABC World News Tonight Sunday - WLS (1980-08-31).mp4"
        self.assertEqual(show_title(STATION, "Abc World News Tonight Sunday Wls", path),
                         "ABC World News Tonight Sunday - WLS")

    def test_a_show_in_its_own_folder_is_titled_by_the_show(self):
        path = ("catalog/vintage/vintage/Mission Impossible (1966)/Season 4/"
                "Mission Impossible S04E15 The Falcon (2).mkv")
        self.assertEqual(show_title(STATION, "Mission Impossible S04E15 The Falcon", path), "Mission Impossible")

    def test_a_shows_episodes_without_season_folders_too(self):
        path = "catalog/vintage/vintage/The Munsters (1964)/The Munsters S01E36.mkv"
        self.assertEqual(show_title(STATION, "The Munsters S01E36", path), "The Munsters")


if __name__ == "__main__":
    unittest.main()
