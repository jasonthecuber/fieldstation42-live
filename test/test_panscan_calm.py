"""fs42_live (#101): pan and scan keeps its framing through fast action and quick cuts."""
import os
import sys

sys.path.append(os.getcwd())
from fs42_live.panscan import _calm


def test_short_shots_keep_the_framing():
    shots = [[0, 0.3, []], [5, 0.7, []], [5.6, 0.3, []], [6.4, 0.7, []], [7.9, 0.7, []]]
    assert [s[1] for s in _calm(shots)] == [0.3, 0.3, 0.3, 0.3, 0.7]


def test_a_shot_with_no_one_keeps_the_framing():
    shots = [[0, 0.3, []], [4, 0.5, []], [9, 0.3, []]]
    assert [s[1] for s in _calm(shots)] == [0.3, 0.3, 0.3]


def test_a_small_change_keeps_the_framing_and_a_big_one_reframes():
    shots = [[0, 0.3, []], [4, 0.36, []], [9, 0.7, []]]
    assert [s[1] for s in _calm(shots)] == [0.3, 0.3, 0.7]


def test_a_pan_carries_into_the_next_shot():
    shots = [[0, 0.3, [[1, 2, 0.7]]], [4, 0.72, []]]
    assert [s[1] for s in _calm(shots)] == [0.3, 0.7]


def test_the_stored_plan_is_left_alone():
    shots = [[0, 0.3, []], [5, 0.7, []], [5.6, 0.3, []]]
    _calm(shots)
    assert shots == [[0, 0.3, []], [5, 0.7, []], [5.6, 0.3, []]]
