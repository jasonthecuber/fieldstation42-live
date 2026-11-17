"""Up next: a little bar in the bottom-left corner naming the show that comes on next, as networks ran
near a show's end. A channel has it with `"up_next": true` in its station conf; it pops up BEFORE seconds
before the show's last part ends (10 minutes, then 2), only over the show itself, never over a break.

The bar is the box's own animation when it has one: ART_DIR/art.mov, a short clip with alpha (qtrle or
png in a .mov), the frame's width, laid over the bottom of the picture, with the title drawn into it. Its
art.json says where the title goes and when it shows in the clip (ART's keys; any left out are ART's).
With no art, a plain dark bar fades in and out instead."""

import datetime
import hashlib
import json
import logging
import os

from fs42_live import guide, messages

_l = logging.getLogger("FS42LIVE")

ART_DIR = "runtime/up_next"
BEFORE = (600, 120)  # seconds before a show ends that it pops up
BREAKS = ("commercial", "bump")
EDGE = 1.0  # kept this far inside a show part, so it never runs into a break
APART = 30.0  # two pop-ups closer than this (a short show) are one
NEAR = 120.0  # how far one may move to fit in the show (half of BEFORE, if more): its last part is often short
TEXTS = "/dev/shm/fs42_up_next"

# where the title goes in art.mov at 640x480, and when it shows: fades in over `in`, out over `out`
ART = {"file": "art.mov", "y": 302, "length": 8.8,
       "text": {"cx": 369, "cy": 387, "w": 364, "size": 34, "color": "0x3a1e11", "shadow": "0xf5c542",
                "in": [1.2, 1.45], "out": [7.62, 7.75]}}
PLAIN = {"length": 8.0, "fade": 0.5}


def wanted(station):
    return bool(station.get("up_next"))


def popups(plan, block_start, length):
    """When the block's pop-ups start (datetimes): BEFORE seconds before its show's last part ends, each
    moved to the nearest time it fits wholly inside a part of the show (not a commercial or bumper)."""
    parts, mark = [], block_start
    for entry in plan:
        end = mark + datetime.timedelta(seconds=entry.duration)
        if getattr(entry, "content_type", None) not in BREAKS:
            parts.append((mark, end))
        mark = end
    if not parts:
        return []
    show_end = parts[-1][1]
    out = []
    for before in BEFORE:
        want = show_end - datetime.timedelta(seconds=before)
        best = None
        for start, end in parts:
            first = start + datetime.timedelta(seconds=EDGE)
            last = end - datetime.timedelta(seconds=EDGE + length)
            if last < first:
                continue  # too short a part to hold it
            at = min(max(want, first), last)
            if best is None or abs((at - want).total_seconds()) < abs((best - want).total_seconds()):
                best = at
        if best is None or abs((best - want).total_seconds()) > max(before / 2, NEAR):
            continue  # nowhere near where it belongs: a short show
        if out and abs((best - out[-1]).total_seconds()) < length + APART:
            out[-1] = best  # one pop-up, the later
            continue
        out.append(best)
    return out


def art():
    """The box's art settings, or None to draw the plain bar."""
    try:
        with open(os.path.join(ART_DIR, "art.json")) as f:
            mine = json.load(f)
    except (OSError, ValueError):
        mine = {}
    conf = {**ART, **mine, "text": {**ART["text"], **mine.get("text", {})}}
    path = os.path.abspath(os.path.join(ART_DIR, conf["file"]))
    return {**conf, "path": path} if os.path.exists(path) else None


def for_segment(station, network, when, duration, manager):
    """[(seconds into the segment, title)] for pop-ups shown during a show segment playing from `when`
    for `duration` seconds; [] for a channel without Up next, or with nothing after it."""
    if not wanted(station):
        return []
    try:
        block = manager.get_programming_block(network, when)
        nxt = manager.get_programming_block(network, block.end_time + datetime.timedelta(seconds=1))
    except Exception:
        return []  # the schedule ends here
    if block is None or nxt is None or not block.plan:
        return []
    title = guide.block_title(station, nxt)
    if not title:
        return []
    look = art()
    length = look["length"] if look else PLAIN["length"]
    out = []
    for at in popups(block.plan, block.start_time, length):
        rel = (at - when).total_seconds()
        if -length < rel < duration:
            out.append((round(rel, 3), title))
    return out


def video_filter(shown, width=640):
    """The filter drawing the pop-ups, `shown` as for_segment gives them; None for none. It starts and
    ends as one chain, so it joins the others with commas."""
    if not shown:
        return None
    look = art()
    chain = []
    for n, (rel, title) in enumerate(shown):
        textfile = _textfile(title)
        if look:
            chain.append(_art_popup(n, rel, textfile, ems(title), look))
        else:
            chain.append(_plain_popup(rel, textfile, width))
    return ",".join(chain)


# Liberation Sans Bold's widths (Arial Bold's), in thousandths of the font size
WIDTHS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
                                                722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611)))
WIDTHS.update(zip("abcdefghijklmnopqrstuvwxyz", (556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889,
                                                611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500)))
WIDTHS.update({" ": 278, "'": 238, ",": 278, ".": 278, ":": 333, "-": 333, "&": 722, "!": 333, "?": 611})


def ems(title):
    """How wide the title is in font sizes, so it's sized to fit (a character not listed counts as wide)."""
    return sum(556 if c.isdigit() else WIDTHS.get(c, 800) for c in title) / 1000


def _art_popup(n, rel, textfile, ems, look):
    t = look["text"]
    clip = (f"movie={_esc(look['path'])},setpts=PTS+{rel:.3f}/TB" if rel >= 0
            else f"movie={_esc(look['path'])},trim=start={-rel:.3f},setpts=PTS-STARTPTS")
    size = int(min(t["size"], t["w"] / max(ems, 0.1)))
    a, b = rel + t["in"][0], rel + t["in"][1]
    c, d = rel + t["out"][0], rel + t["out"][1]
    alpha = f"if(lt(t,{b:.3f}),(t-{a:.3f})/{b - a:.3f},if(gt(t,{c:.3f}),({d:.3f}-t)/{d - c:.3f},1))"
    return (f"null[un{n}];{clip}[unart{n}];"
            f"[un{n}][unart{n}]overlay=x=0:y={look['y']}:eof_action=pass,"
            f"drawtext=fontfile={messages.FONT}:textfile={textfile}:expansion=none:fontsize={size}:"
            f"fontcolor={t['color']}:shadowcolor={t['shadow']}:shadowx=2:shadowy=2:"
            f"x={t['cx']}-tw/2:y={t['cy']}+{0.36 * size:.1f}-ascent:"
            f"enable='between(t,{a:.3f},{d:.3f})':alpha='{alpha}'")


def _plain_popup(rel, textfile, width):
    length, fade = PLAIN["length"], PLAIN["fade"]
    a, d = rel, rel + length
    on = f"between(t,{a:.3f},{d:.3f})"
    alpha = f"min(1,min((t-{a:.3f})/{fade},({d:.3f}-t)/{fade}))"
    return (f"drawbox=x=24:y=392:w={width // 2}:h=44:color=black@0.6:t=fill:enable='{on}',"
            f"drawtext=fontfile={messages.FONT}:text=UP NEXT:fontsize=14:fontcolor=yellow:"
            f"x=34:y=398:enable='{on}':alpha='{alpha}',"
            f"drawtext=fontfile={messages.FONT}:textfile={textfile}:expansion=none:fontsize=20:"
            f"fontcolor=white:x=34:y=413:enable='{on}':alpha='{alpha}'")


def _textfile(title):
    os.makedirs(TEXTS, exist_ok=True)
    path = os.path.join(TEXTS, hashlib.sha1(title.encode()).hexdigest()[:16] + ".txt")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(title)
    return path


def _esc(path):
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")
