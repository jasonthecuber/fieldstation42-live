import datetime
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules.setdefault("ffmpeg", MagicMock())
sys.modules.setdefault("moviepy", MagicMock())
sys.modules.setdefault("moviepy.editor", MagicMock())

from fs42.catalog import ShowCatalog  # noqa: E402
from fs42.catalog_entry import MatchingContentNotFound  # noqa: E402
from fs42.liquid_blocks import LiquidBlock  # noqa: E402

SUNDAY_9AM = datetime.datetime(2026, 11, 22, 9, 0)


def _catalog():
    catalog = ShowCatalog.__new__(ShowCatalog)
    catalog.config = {
        "network_name": "T", "break_strategy": "standard", "break_duration": 120,
        "commercial_free": False, "bump_dir": "bump", "sunday": {"9": {"tags": "drama"}},
    }
    catalog.clip_index = {
        "bump": [SimpleNamespace(path="bump.mp4", duration=5, hints=[], tag="bump", count=0, content_type="bump", media_type="video")],
        "bump-prebump": [], "bump-postbump": [],
    }
    catalog._l = logging.getLogger("test")
    catalog.min_gap = 3

    def find_commercial(seconds, when, commercial_dir=None):
        if seconds < 30:
            raise MatchingContentNotFound("nothing fits")
        return SimpleNamespace(path="ad.mp4", duration=30, content_type="commercial", media_type="video")

    catalog.find_commercial = find_commercial
    return catalog


def _segments(show_minutes, slot_minutes, breaks=None):
    show = SimpleNamespace(path="show.mp4", realpath="show.mp4", duration=show_minutes * 60, title="Show", content_type="feature", media_type="video")
    break_info = {"start_bump": None, "end_bump": None, "bump_dir": None, "commercial_dir": None}
    block = LiquidBlock(show, SUNDAY_9AM, SUNDAY_9AM + datetime.timedelta(minutes=slot_minutes), "Show", "standard", break_info)
    fluid = MagicMock()
    fluid.return_value.get_chapters.return_value = []
    fluid.return_value.get_breaks.return_value = breaks or []
    with patch("fs42.liquid_blocks.FluidBuilder", fluid):
        block.make_plan(_catalog())
    segments, in_show = 0, False
    for entry in block.plan:
        if entry.path == "show.mp4" and not in_show:
            segments += 1
        in_show = entry.path == "show.mp4"
    return segments


def test_hour_drama_without_break_spots_gets_a_break_every_quarter_hour_not_every_few_minutes():
    # #120: a 52-minute The Penguin in an hour slot had 8 breaks, one every 6 minutes
    assert _segments(52, 60) == 3


def test_sitcom_without_break_spots_still_breaks_in_the_middle():
    assert _segments(22, 30) == 3


def test_a_little_spare_time_makes_one_break_after_the_show():
    assert _segments(57, 60) == 1


def test_black_spots_still_decide_the_breaks():
    spots = [{"chapter_start": s, "chapter_end": e} for s, e in [(0, 600), (600, 1200), (1200, 1800), (1800, 2400), (2400, 3120)]]
    assert _segments(52, 60, spots) == 5
