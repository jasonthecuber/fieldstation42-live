import datetime

from fs42 import schedule_hint
from fs42.catalog import ShowCatalog
from fs42.catalog_entry import CatalogEntry
from fs42.media_processor import MediaProcessor

WHEN = datetime.datetime(2026, 11, 21, 20)


def _catalog():
    catalog = ShowCatalog({"network_name": "test", "bump_dir": "bump"}, load=False)
    pre = "bump-" + ShowCatalog.prebump
    catalog.clip_index = {
        "bump": [],
        pre: [
            CatalogEntry("bump/pre/Before break.mp4", 8, pre, hints=[schedule_hint.BumpHint("pre")]),
            CatalogEntry("bump/pre/during Miami Vice/kron_1986_boat_1.mp4", 7, pre,
                         hints=[schedule_hint.BumpHint("pre"), schedule_hint.ShowHint("during Miami Vice")]),
        ],
    }
    return catalog


def _picks(catalog, lookahead):
    return {catalog.find_bump(60, WHEN, ShowCatalog.prebump, lookahead=lookahead).title for _ in range(40)}


def test_a_during_folder_is_a_show_hint_only_in_bump_dirs():
    hints = MediaProcessor._process_hints("catalog/primetime/bump/pre/during miamivice", "bump", bumpdir=True)
    assert [type(h).__name__ for h in hints] == ["ShowHint"]
    assert MediaProcessor._process_hints("catalog/primetime/during miamivice", "x") == []


def test_the_show_matches_its_tag_whatever_the_spelling():
    hint = schedule_hint.ShowHint("during Miami Vice")
    assert hint.fits("miamivice") and hint.fits("Miami_Vice")
    assert not hint.fits("missionimpossible") and not hint.fits(None)


def test_a_shows_own_bumper_plays_only_in_that_shows_breaks():
    catalog = _catalog()
    assert _picks(catalog, ["missionimpossible", None, None, None]) == {"Before break"}
    assert _picks(catalog, None) == {"Before break"}  # show not known: left out
    assert _picks(catalog, ["miamivice", None, None, None]) == {"Before break", "kron_1986_boat_1"}


def test_with_only_another_shows_bumper_the_break_has_none():
    catalog = _catalog()
    catalog.clip_index["bump-" + ShowCatalog.prebump].pop(0)
    assert catalog._bump_or_none(60, WHEN, ShowCatalog.prebump, None, ["ateam", None, None, None]) is None


def test_the_hint_survives_the_catalog():
    original = schedule_hint.ShowHint("during Miami Vice")
    rebuilt = schedule_hint.HINT_KLASS_BY_TYPE["show"].fromJSON(original.toJSON())
    assert rebuilt.toJSON() == original.toJSON() and rebuilt.fits("miamivice")
