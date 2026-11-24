import sqlite3

import numpy as np

from fs42.fluid_statements import FluidStatements
from fs42.play_cuts import in_content
from fs42_live import uploader_cards as uc

FPS = uc.FPS
rng = np.random.default_rng(42)


def _pic():
    return rng.integers(30, 220, (uc.H, uc.W, 3), dtype=np.uint8)


CARD = _pic()
SLATE = _pic()
REF = uc.grey(CARD[None])


def _frames(parts):
    """Frames FPS a second of (seconds, kind) parts: card, wipe, slate, black, show."""
    out = []
    for secs, kind in parts:
        for _ in range(int(secs * FPS)):
            if kind == "card":
                out.append(np.clip(CARD.astype(int) + rng.integers(-40, 40), 0, 255).astype(np.uint8))  # brighter or darker
            elif kind == "slate":
                out.append(SLATE)
            elif kind == "black":
                f = np.zeros_like(CARD)
                f[-1, :5] = 255  # a line of tape noise
                out.append(f)
            else:
                out.append(_pic())
    return np.array(out)


def _start(parts):
    f = _frames(parts)
    return uc.start_of(f, uc.is_card(f, REF))


def test_the_show_starts_after_the_card_its_slate_and_the_black():
    assert _start([(0.5, "black"), (3, "card"), (1, "wipe"), (6, "slate"), (1.5, "black"), (30, "show")]) == 12.0


def test_with_no_black_the_show_starts_after_the_slate():
    assert _start([(3, "card"), (1, "wipe"), (6, "slate"), (30, "show")]) == 10.0


def test_with_nothing_between_the_show_starts_after_the_card():
    assert _start([(3, "card"), (40, "show")]) == 3.0


def test_with_no_card_the_show_starts_at_the_start():
    assert _start([(1, "black"), (6, "slate"), (1, "black"), (30, "show")]) == 0.0
    # a card well into the file is the show's own
    assert _start([(15, "show"), (3, "card"), (20, "show")]) == 0.0


def test_the_show_ends_where_the_card_starts_and_the_black_before_it():
    f = _frames([(60, "show"), (1, "black"), (4, "card"), (25, "show")])  # the card's end: not like it
    assert uc.end_of(f, uc.is_card(f, REF), 1000.0) == 1060.0
    f = _frames([(90, "show")])
    assert uc.end_of(f, uc.is_card(f, REF), 1000.0) is None


def test_where_it_starts_is_a_cut_for_that_file_only():
    db = sqlite3.connect(":memory:")
    FluidStatements.init_db(db)
    FluidStatements.add_play_cuts(db, "/hs.mp4", [(600.0, 700.0)], 1400.0)
    FluidStatements.add_play_start(db, "/hs.mp4", 16.5, 1400.0)
    assert FluidStatements.get_play_cuts(db, "/hs.mp4", 1400.0) == [(0.0, 16.5), (600.0, 700.0)]
    assert FluidStatements.get_play_cuts(db, "/hs.mp4", 1500.0) == []  # replaced by another file
    FluidStatements.add_play_start(db, "/other.mp4", 0.0, 900.0)  # looked at: no card
    assert FluidStatements.get_play_cuts(db, "/other.mp4", 900.0) == []


def test_the_shows_own_break_points_move_with_a_card_cut_off_its_start():
    points = [{"chapter_start": 10.0}, {"chapter_start": 416.5}, {"chapter_start": 916.5}]
    assert in_content(points, [(0.0, 16.5)]) == [{"chapter_start": 400.0}, {"chapter_start": 900.0}]
