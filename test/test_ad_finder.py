import numpy as np

from fs42_live import ad_finder as af

FPS = af.FPS


def _tape(parts, dur=1800):
    """(score, show, ad) for a tape made of (seconds, kind) parts: show, ad, plug, credits"""
    score, show, ad = [], [], []
    for secs, kind in parts:
        n = int(secs * FPS)
        score += [{"show": 0.0, "ad": 0.9, "plug": 0.9, "credits": 0.9, "quiet_ad": 0.15}[kind]] * n
        show += [kind == "show"] * n
        ad += [kind == "ad"] * n
    assert len(score) == dur * FPS
    return np.array(score, np.float32), np.array(show), np.array(ad)


def _secs(found):
    return [(i / FPS, j / FPS) for i, j in found]


def test_a_break_of_recorded_ads_is_found():
    s = _tape([(400, "show"), (120, "ad"), (1250, "show"), (30, "credits")])
    assert _secs(af.breaks(*s, 1800)) == [(400, 520)]


def test_an_ad_that_looks_like_the_show_doesnt_split_a_break():
    s = _tape([(400, "show"), (60, "ad"), (6, "quiet_ad"), (60, "ad"), (1244, "show"), (30, "credits")])
    assert _secs(af.breaks(*s, 1800)) == [(400, 526)]


def test_the_show_between_two_breaks_stays():
    # 15 s of the show between two breaks, its bug on for some of it: not joined
    s = _tape([(400, "show"), (100, "ad"), (15, "show"), (60, "ad"), (1195, "show"), (30, "credits")])
    off = ~s[1]
    off[1000:1020] = True  # the bug comes back late
    assert _secs(af.breaks(*s, 1800, off)) == [(400, 500), (515, 575)]


def test_ads_that_look_like_the_show_with_the_bug_off_all_through_are_one_break():
    # 40 s of a plain ad (a phone number on a card) in the middle of a break
    parts = [(400, "show"), (100, "ad"), (40, "show"), (60, "ad"), (1170, "show"), (30, "credits")]
    score, show, ad = _tape(parts)
    show[1000:1080] = False
    off = ~show
    assert _secs(af.breaks(score, show, ad, 1800, off)) == [(400, 600)]
    # with the bug coming on in that time, it's the show
    off[1040:1044] = False
    show[1040:1044] = True
    assert _secs(af.breaks(score, show, ad, 1800, off)) == [(400, 500), (540, 600)]


def test_a_break_with_no_newer_sound_and_the_bug_off_all_through_is_found():
    # a tape whose ads sound as old as the show: a full break with the bug off is still a break
    s = _tape([(400, "show"), (130, "plug"), (1240, "show"), (30, "credits")])
    assert _secs(af.breaks(*s, 1800)) == [(400, 530)]
    # the bug on through it (only unsure keyframes): the show's own
    off = ~s[1]
    off[800:1060:4] = False
    assert af.breaks(*s, 1800, off) == []


def test_a_break_joined_on_into_the_end_of_the_show_gives_back_the_joined_part():
    score, show, ad = _tape([(1500, "show"), (20, "ad"), (40, "plug"), (12, "quiet_ad"), (198, "plug"), (30, "credits")])
    off = ~show
    off[3144:3540:10] = False  # the bug now and then: the show, with its end credits
    assert _secs(af.breaks(score, show, ad, 1800, off)) == [(1500, 1560)]


def test_a_moment_of_the_show_that_looks_like_an_ad_doesnt_join_a_break():
    score = np.zeros(1000, np.float32)
    off = np.ones(1000, bool)
    score[100:400] = 0.9  # the break
    score[430:440] = 0.6  # 5 s of the show, after 15 s that look nothing like ads
    assert not af._joins_over_bug_off(score, off, [100, 400], [430, 440])
    assert af._joins_over_bug_off(score, off, [100, 400], [420, 430])  # 10 s after: still the break
    score[400:430] = 0.15  # a bit ad-like between: still the break
    assert af._joins_over_bug_off(score, off, [100, 400], [430, 440])
    # before a break: 6 s that look only a bit like ads, then the break
    score[:] = 0
    score[80:93] = 0.4
    score[115:400] = 0.9
    assert not af._joins_over_bug_off(score, off, [80, 93], [115, 400])
    score[80:93] = 0.8  # a short ad, then the rest of the break
    assert af._joins_over_bug_off(score, off, [80, 93], [115, 400])


def test_the_shows_own_prize_plugs_stay():
    s = _tape([(400, "show"), (120, "ad"), (1200, "show"), (50, "plug"), (30, "credits")])
    assert _secs(af.breaks(*s, 1800)) == [(400, 520)]


def test_the_lead_in_and_the_end_credits_stay():
    s = _tape([(30, "ad"), (1730, "show"), (40, "credits")])
    assert af.breaks(*s, 1800) == []


def test_a_break_running_into_the_credits_ends_where_it_looks_least_like_ads():
    score, show, ad = _tape([(1640, "show"), (130, "ad"), (30, "credits")])
    score[1770 * FPS:1772 * FPS] = 0.05  # the cut from the last ad to the credits
    found = _secs(af.breaks(score, show, ad, 1800))
    assert len(found) == 1 and found[0][0] == 1640 and 1769 <= found[0][1] <= 1772


def test_a_break_starts_at_the_black_after_the_shows_own_going_to_break_shot():
    # the run starts at 910 (the show's wide shot looks like an ad); black at 913.6
    assert af.edges(910 * FPS, 1050 * FPS, 3520, [(913.6, 914.2), (1049.6, 1050.4)], [], 1800) == (913.6, 1050.4)
    # no black: just before the run; quiet near the start counts too
    assert af.edges(910 * FPS, 1050 * FPS, 3520, [], [], 1800) == (909.75, 1050)
    assert af.edges(910 * FPS, 1050 * FPS, 3520, [], [909.5], 1800) == (909.5, 1050)


def test_spans():
    assert af.spans(np.array([0, 1, 1, 0, 1], bool), 0.5) == [(0.5, 1.5), (2.0, 2.5)]
