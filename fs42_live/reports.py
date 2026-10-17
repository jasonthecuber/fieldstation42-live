"""Problem reports from viewers ("Report a problem" in the web player's menu).

Each report is saved with what the channel's schedule played in the half minute before the
viewer opened the menu, since the problem is usually a few seconds back by then (and the
picture runs a few seconds behind the schedule).
"""

import datetime
import json
import os
import threading

from fs42.liquid_manager import LiquidManager

STORE = "runtime/reports.jsonl"  # one report per line, oldest first
LOOK_BACK = 35  # seconds of schedule saved before the menu opened
PICKS = {1: "Wrong bumper or ad", 2: "Static or frozen", 3: "Sound problem", 4: "Wrong show or listing", 5: "Something else"}
NUDGE = datetime.timedelta(milliseconds=1)  # at exactly a block's end the schedule has no answer

_lock = threading.Lock()


def what_played(network, until, seconds=LOOK_BACK):
    """The files the schedule played in the `seconds` before `until`, with the parts of them."""
    played = []
    when = until - datetime.timedelta(seconds=seconds)
    while when < until and len(played) < 50:
        try:
            pp = LiquidManager().get_play_point(network, when + NUDGE)
        except Exception as e:  # no schedule there
            played.append({"error": str(e)})
            break
        entry = pp.plan[pp.index]
        left = entry.duration - pp.offset
        upto = min(left, (until - when).total_seconds())
        played.append({"file": entry.path, "from": round(entry.skip + pp.offset, 1),
                       "to": round(entry.skip + pp.offset + upto, 1),
                       "at": when.isoformat(timespec="seconds")})
        when += datetime.timedelta(seconds=max(left, 0.1))
    return played


def save(station, pick, note, ago, agent):
    seen = datetime.datetime.now() - datetime.timedelta(seconds=max(0.0, min(ago, 3600)))
    report = {
        "seen": seen.isoformat(timespec="seconds"),
        "channel": station["channel_number"],
        "network": station["network_name"],
        "problem": PICKS.get(pick, ""),
        "note": note.strip()[:1000],
        "device": agent[:200],
        "played": [] if station["network_type"] == "web" else what_played(station["network_name"], seen),
    }
    with _lock:
        with open(STORE, "a") as f:
            f.write(json.dumps(report) + "\n")
    return report


def recent(count=50):
    try:
        with open(STORE) as f:
            lines = f.readlines()[-count:]
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in reversed(lines) if line.strip()]
