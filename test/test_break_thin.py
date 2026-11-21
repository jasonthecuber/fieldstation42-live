from fs42_live.break_finder import _thin


def points(fades, duration=3000.0):
    return [p for p, _ in _thin(fades, duration)]


def test_of_a_teaser_fade_and_the_titles_a_minute_later_the_later_one_is_the_break():
    assert points([(40.0, 0.8), (100.0, 1.0), (900.0, 1.5), (1800.0, 1.5)]) == [100.0, 900.0, 1800.0]


def test_a_much_weaker_dark_moment_just_after_a_clear_fade_isnt_the_break():
    # Mission Impossible: the teaser fades out at 0:50; at 1:23, a dark, hushed room in the next scene
    assert points([(50.4, 1.23), (83.0, 0.41), (900.0, 1.5), (1800.0, 1.5)]) == [50.4, 900.0, 1800.0]


def test_found_breaks_further_on_stay_when_an_opening_is_looked_at_again():
    inf = float("inf")
    assert points([(50.4, 1.23), (83.0, 0.41), (900.0, inf), (1800.0, inf)]) == [50.4, 900.0, 1800.0]
