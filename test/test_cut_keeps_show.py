"""A gone ad or bumper doesn't make the channel plan what's on now again (#97)."""

from types import SimpleNamespace

from fs42.block_plan import BlockPlanEntry
from fs42_live import schedule_keeper


def test_gone_ad_or_bumper_keeps_the_show(tmp_path):
    show = tmp_path / "Bewitched S05E19.mp4"
    show.write_bytes(b"x")
    block = SimpleNamespace(plan=[
        BlockPlanEntry(str(show), 0, 600),
        BlockPlanEntry(str(tmp_path / "gone ad.mp4"), 0, 30, content_type="commercial"),
        BlockPlanEntry(str(tmp_path / "gone bumper.mp4"), 0, 8, content_type="bump"),
        BlockPlanEntry(str(show), 600, 600),
    ])
    assert not schedule_keeper._gone(block)


def test_gone_show_is_gone(tmp_path):
    block = SimpleNamespace(plan=[BlockPlanEntry(str(tmp_path / "deleted episode.mp4"), 0, 600)])
    assert schedule_keeper._gone(block)
