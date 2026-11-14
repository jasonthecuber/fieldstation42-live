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
PICKS = {1: "Bumper or ad problem", 2: "Static or frozen", 3: "Sound problem", 4: "Wrong show or listing", 5: "Something else"}
NUDGE = datetime.timedelta(milliseconds=1)  # at exactly a block's end the schedule has no answer
# "Bumper or ad problem", about one picked by name (#90): what's wrong with it. The first takes it off the box
ISSUES = {1: "Take it off this box", 2: "Wrong channel for it", 3: "Picture and sound out of step",
          4: "Played at the wrong time (not at a break)", 5: "Something else"}
CLIPS_BACK = 240  # seconds of schedule looked through for the ads and bumpers to pick from
CLIPS_MOST = 6
UNSEEN = 4  # seconds: one the schedule started this close to the menu opening isn't on the screen yet

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


def clip_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def recent_clips(network, until, seconds=CLIPS_BACK, most=CLIPS_MOST):
    """The commercials and bumpers the schedule played in the `seconds` before `until`, the latest first,
    each once: [{"file", "name", "kind" ("commercial" or "bump")}]."""
    found, when = [], until - datetime.timedelta(seconds=seconds)
    last = until - datetime.timedelta(seconds=UNSEEN)
    while when < last and len(found) < 400:
        try:
            pp = LiquidManager().get_play_point(network, when + NUDGE)
        except Exception:  # no schedule there
            break
        entry = pp.plan[pp.index]
        if entry.content_type in ("commercial", "bump") and not entry.is_stream:
            found.append({"file": entry.path, "name": clip_name(entry.path), "kind": entry.content_type})
        when += datetime.timedelta(seconds=max(entry.duration - pp.offset, 0.1))
    out, seen = [], set()
    for c in reversed(found):
        if c["file"] not in seen and os.path.isfile(c["file"]):
            seen.add(c["file"])
            out.append(c)
    return out[:most]


def is_clip(station, path):
    """"commercial" or "bump" if path is one of the station's (in its commercials or bumpers folder), else None."""
    if not path or not os.path.isfile(path):
        return None
    folder = os.path.abspath(station.get("content_dir") or "")
    full = os.path.abspath(path)
    for kind, key in (("commercial", "commercial_dir"), ("bump", "bump_dir")):
        sub = station.get(key)
        if station.get("content_dir") and sub:
            top = os.path.join(folder, sub)
            if os.path.commonpath([top, full]) == top:
                return kind
    return None


def save(station, pick, note, ago, agent, ondemand=None, clip=None, issue=0):
    """`ondemand` is (file, seconds into it) when the viewer was watching on demand, not the channel.
    `clip`, the commercial or bumper the report is about, picked by name, with `issue` (ISSUES)."""
    seen = datetime.datetime.now() - datetime.timedelta(seconds=max(0.0, min(ago, 3600)))
    report = {
        "seen": seen.isoformat(timespec="seconds"),
        "channel": station["channel_number"],
        "network": station["network_name"],
        "problem": PICKS.get(pick, ""),
        "note": note.strip()[:1000],
        "device": agent[:200],
        "played": played(station, seen, ondemand),
    }
    if clip:
        report.update(clip={"file": clip, "name": clip_name(clip), "kind": is_clip(station, clip)},
                      issue=ISSUES.get(issue, ""))
    with _lock:
        with open(STORE, "a") as f:
            f.write(json.dumps(report) + "\n")
        with open(STORE) as f:
            report["number"] = sum(1 for _ in f)  # shown to the viewer, so they know it arrived
    return report


def recent(count=50):
    try:
        with open(STORE) as f:
            lines = f.readlines()[-count:]
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in reversed(lines) if line.strip()]


def played(station, seen, ondemand):
    if ondemand:
        file, at = ondemand
        return [{"file": file, "from": round(max(0.0, at - LOOK_BACK), 1), "to": round(at, 1),
                 "at": seen.isoformat(timespec="seconds"), "ondemand": True}]
    return [] if station["network_type"] == "web" else what_played(station["network_name"], seen)
