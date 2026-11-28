import json
import os
import random
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("ffmpeg", MagicMock())
sys.modules.setdefault("moviepy", MagicMock())
sys.modules.setdefault("moviepy.editor", MagicMock())

from fs42.catalog import ShowCatalog  # noqa: E402
from fs42_live.tools import seasons  # noqa: E402


def _ad(path, duration=30):
    return SimpleNamespace(path=path, duration=duration, hints=[], count=0, realpath=None)


def _catalog(ads):
    catalog = ShowCatalog.__new__(ShowCatalog)
    catalog.config = {"commercial_dir": "commercial"}
    catalog.clip_index = {"commercial": ads}
    return catalog


def _pool(tmp_path, regular=100, seasonal=400, share="0.2"):
    os.makedirs(tmp_path / "commercial" / "christmas")
    if share is not None:
        (tmp_path / "commercial" / "christmas" / ShowCatalog.SHARE_FILE).write_text(share)
    return ([_ad(f"{tmp_path}/commercial/classics/{i}.mp4") for i in range(regular)]
            + [_ad(f"{tmp_path}/commercial/christmas/{i}.mp4") for i in range(seasonal)])


def _christmas_part(catalog, picks=4000):
    random.seed(1)
    got = [catalog.find_commercial(60, None, None).path for _ in range(picks)]
    return sum("/christmas/" in p for p in got) / picks


def test_a_folder_with_a_share_gets_that_share_however_many_ads_it_holds(tmp_path):
    assert 0.17 < _christmas_part(_catalog(_pool(tmp_path))) < 0.23


def test_without_a_share_every_ad_is_as_likely(tmp_path):
    assert 0.77 < _christmas_part(_catalog(_pool(tmp_path, share=None))) < 0.83


def test_when_the_share_side_has_nothing_short_enough_the_others_play(tmp_path):
    ads = _pool(tmp_path, regular=5, seasonal=5)
    for ad in ads[5:]:
        ad.duration = 90
    assert _christmas_part(_catalog(ads), picks=200) == 0


def test_a_bad_share_file_is_no_share(tmp_path):
    assert 0.77 < _christmas_part(_catalog(_pool(tmp_path, share="lots"))) < 0.83


def test_seasons_writes_the_share_while_in_season_and_takes_it_away_after(tmp_path):
    try:
        os.symlink(tmp_path, tmp_path / "can-link")
    except OSError:
        pytest.skip("can't make links here")
    data, root = tmp_path / "data", tmp_path / "media"
    os.makedirs(root / "commercials" / "christmas")
    (root / "commercials" / "christmas" / "ad.mp4").write_text("")
    os.makedirs(data / "confs")
    season = {"from": "11-27", "until": "01-01",
              "links": [{"from": "commercials/christmas", "to": "tv/commercial/christmas", "share": 0.2}]}
    share_file = data / "catalog" / "tv" / "commercial" / "christmas" / seasons.SHARE_FILE
    assert seasons.SHARE_FILE == ShowCatalog.SHARE_FILE

    state = {}
    touched = seasons.sync_shares(state, seasons.wanted_shares(str(data), [season]), False)
    touched |= seasons.sync_links(state, seasons.wanted_links(str(data), str(root), [season]), False)
    assert share_file.read_text() == "0.2"
    assert touched == {str(share_file.parent)}
    assert seasons.sync_shares(state, seasons.wanted_shares(str(data), [season]), False) == set()  # no change

    json.dumps(state)
    seasons.sync_shares(state, seasons.wanted_shares(str(data), []), False)
    seasons.sync_links(state, seasons.wanted_links(str(data), str(root), []), False)
    assert not share_file.parent.exists()  # emptied, so the folder goes too
