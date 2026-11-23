import sqlite3
from types import SimpleNamespace as NS

from fs42.fluid_statements import FluidStatements
from fs42.liquid_blocks import LiquidBlock
from fs42.block_plan import BlockPlanEntry
from fs42.play_cuts import clean, cut_total, to_content, to_file, cut_spots, file_pieces

# a tape: show 0-400, ad 400-500, show 500-800, ad 800-900, show 900-1800
CUTS = [(400.0, 500.0), (800.0, 900.0)]


def test_content_time_leaves_the_ads_out():
    assert cut_total(CUTS, 1800.0) == 200.0
    assert to_content(300.0, CUTS) == 300.0
    assert to_content(450.0, CUTS) is None
    assert to_content(600.0, CUTS) == 500.0
    assert to_file(500.0, CUTS) == 600.0
    # the ads were where the show had its breaks
    assert cut_spots(CUTS) == [400.0, 700.0]


def test_a_stretch_of_the_show_jumps_over_the_ads():
    assert file_pieces(0.0, 1600.0, CUTS) == [(0.0, 400.0), (500.0, 300.0), (900.0, 900.0)]
    assert file_pieces(400.0, 300.0, CUTS) == [(500.0, 300.0)]
    assert file_pieces(350.0, 100.0, CUTS) == [(350.0, 50.0), (500.0, 50.0)]


def test_cuts_are_tidied():
    assert clean([(800, 900), (390, 420), (400, 500), (1750, 1900)], 1800) == \
        [(390, 500), (800, 900), (1750, 1800)]


def test_cuts_are_stored_for_the_length_the_file_had():
    db = sqlite3.connect(":memory:")
    FluidStatements.init_db(db)
    FluidStatements.add_play_cuts(db, "/tape.mp4", CUTS, 1800.0)
    assert FluidStatements.get_play_cuts(db, "/tape.mp4", 1800.2) == CUTS
    # replaced under the same name: not the same tape
    assert FluidStatements.get_play_cuts(db, "/tape.mp4", 1500.0) == []
    assert FluidStatements.get_play_cuts(db, "/other.mp4", 1800.0) == []


def test_a_planned_show_plays_round_the_ads():
    show = NS(path="/tape.mp4", content_type="feature")
    block = LiquidBlock.__new__(LiquidBlock)
    block.content = show
    plan = [BlockPlanEntry("/tape.mp4", 0.0, 400.0), BlockPlanEntry("/ad.mp4", 0, 30, content_type="commercial"),
            BlockPlanEntry("/tape.mp4", 400.0, 300.0), BlockPlanEntry("/ad.mp4", 0, 30, content_type="commercial"),
            BlockPlanEntry("/tape.mp4", 700.0, 900.0)]
    out = [(e.path, e.skip, e.duration) for e in block.skip_cuts(plan, CUTS)]
    assert out == [("/tape.mp4", 0.0, 400.0), ("/ad.mp4", 0, 30), ("/tape.mp4", 500.0, 300.0),
                   ("/ad.mp4", 0, 30), ("/tape.mp4", 900.0, 900.0)]
