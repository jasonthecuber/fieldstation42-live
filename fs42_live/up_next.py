"""Up next: a little bar in the bottom-left corner naming the show that comes on next, as networks ran
near a show's end. A channel has it with `"up_next": true` in its station conf; it pops up BEFORE seconds
before the show's last part ends (10 minutes, then 2), only over the show itself, never over a break.

The bar is the box's own animation when it has one: ART_DIR/art.mov, a short clip with alpha (qtrle or
png in a .mov), the frame's width, laid over the bottom of the picture, with the title drawn into it. Its
art.json says where the title goes and when it shows in the clip (ART's keys; any left out are ART's).
With no art, a plain dark bar fades in and out instead.

Above the bar, a little picture plays a few silent seconds of the next show, from somewhere in its middle
(a different spot each time it pops up). art.json's "preview" moves or sizes it, or false turns it off."""

import datetime
import hashlib
import json
import logging
import os
import random
import re

from fs42_live import guide, messages

_l = logging.getLogger("FS42LIVE")

ART_DIR = "runtime/up_next"
BEFORE = (600, 120)  # seconds before a show ends that it pops up
BREAKS = ("commercial", "bump")
EDGE = 1.0  # kept this far inside a show part, so it never runs into a break
APART = 30.0  # two pop-ups closer than this (a short show) are one
NEAR = 120.0  # how far one may move to fit in the show (half of BEFORE, if more): its last part is often short
TEXTS = "/dev/shm/fs42_up_next"

# where the title goes in art.mov at 640x480 (its full size), and when it shows: fades in over `in`, out
# over `out`. The art is drawn `scale` its size, kept to the bottom-left corner: moved right `left` and
# up `lift` (at 640x480), with everything placed in it (the title, the preview) moving with it.
ART = {"file": "art.mov", "y": 302, "length": 8.8, "scale": 0.55, "left": 16, "lift": 10,
       "text": {"cx": 369, "cy": 387, "w": 364, "size": 34, "color": "0x3a1e11", "shadow": "0xf5c542",
                "in": [1.2, 1.45], "out": [7.62, 7.75]}}
PLAIN = {"length": 8.0, "fade": 0.5}
HEIGHT = 480  # the picture's height where these are drawn
# the picture of the next show, behind the art: its left edge where the bar's rounded end straightens (x),
# its bottom tucked behind the bar's top (bottom), w x h big (all in the art's full size); its outline
# `border` thick; when in the pop-up
PREVIEW = {"x": 146, "bottom": 353, "w": 256, "h": 192, "border": 3, "color": "0x3a1e11", "rise": 10,
           "in": 1.5, "out": 7.3, "fade": 0.3}
# without art: drawn as is (x, y at 640x480) above the plain bar
PLAIN_PREVIEW = {**PREVIEW, "x": 24, "y": 258, "w": 160, "h": 120, "border": 4, "color": "black",
                 "in": 0.6, "out": 7.4}
MIDDLE = (0.3, 0.7)  # the preview comes from this far through the next show
STILL = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


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
    pv = mine.get("preview", {})
    conf["preview"] = _placed({**PREVIEW, **pv}, conf) if pv is not False else None
    path = os.path.abspath(os.path.join(ART_DIR, conf["file"]))
    return {**conf, "path": path} if os.path.exists(path) else None


def _at(conf, x, y):
    """Where a point of the full-size art lands on the picture."""
    k = conf["scale"]
    return conf["left"] + x * k, HEIGHT - conf["lift"] - (HEIGHT - y) * k


def _placed(pv, conf):
    """The preview's settings in the picture's pixels, from the art's."""
    k = conf["scale"]
    w, h = 2 * round(pv["w"] * k / 2), 2 * round(pv["h"] * k / 2)
    x, bottom = _at(conf, pv["x"], pv["bottom"])
    return {**pv, "w": w, "h": h, "x": round(x), "y": round(bottom) - h - 2 * pv["border"]}


def preview_spot(plan, seed, length):
    """(file, seconds into it) for `length` seconds of the show in `plan`, from somewhere in its middle
    picked by `seed`; None if it has nothing to show (a stream, stills, parts too short)."""
    parts, mark = [], 0.0
    for entry in plan:
        path = getattr(entry, "path", None)
        if (getattr(entry, "content_type", None) not in BREAKS and path and not getattr(entry, "is_stream", False)
                and not path.lower().endswith(STILL) and entry.duration >= length + 4):
            parts.append((mark, entry))
        if getattr(entry, "content_type", None) not in BREAKS:
            mark += entry.duration
    if not parts:
        return None
    want = random.Random(seed).uniform(*MIDDLE) * mark
    start, entry = min(parts, key=lambda p: 0 if p[0] <= want <= p[0] + p[1].duration
                       else min(abs(want - p[0]), abs(want - p[0] - p[1].duration)))
    into = min(max(want - start, 2.0), entry.duration - length - 2.0)
    return os.path.abspath(entry.path), round(getattr(entry, "skip", 0) + into, 3)


def for_segment(station, network, when, duration, manager):
    """[(seconds into the segment, title, preview)] for pop-ups shown during a show segment playing from
    `when` for `duration` seconds, preview as preview_spot gives it; [] for a channel without Up next, or
    with nothing after it."""
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
    pv = look["preview"] if look else PLAIN_PREVIEW
    out = []
    for at in popups(block.plan, block.start_time, length):
        rel = (at - when).total_seconds()
        if -length < rel < duration:
            spot = pv and preview_spot(nxt.plan or [], f"{nxt.start_time}|{at}", pv["out"] - pv["in"])
            out.append((round(rel, 3), title, spot or None))
    return out


def video_filter(shown, width=640):
    """The filter drawing the pop-ups, `shown` as for_segment gives them; None for none. It starts and
    ends as one chain, so it joins the others with commas."""
    if not shown:
        return None
    look = art()
    chain = []
    for n, (rel, title, *more) in enumerate(shown):
        textfile = _textfile(title)
        spot = more[0] if more else None
        pv = look["preview"] if look else PLAIN_PREVIEW
        if spot and pv:
            chain.append(_preview(n, rel, spot, pv))
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
    t, k = look["text"], look["scale"]
    clip = (f"movie={_esc(look['path'])},setpts=PTS+{rel:.3f}/TB" if rel >= 0
            else f"movie={_esc(look['path'])},trim=start={-rel:.3f},setpts=PTS-STARTPTS")
    if k != 1:
        clip += f",scale=iw*{k}:ih*{k}"
    x0, y0 = _at(look, 0, look["y"])
    cx, cy = _at(look, t["cx"], t["cy"])
    size = int(min(t["size"] * k, t["w"] * k / max(ems, 0.1)))
    shadow = max(1, round(2 * k))
    a, b = rel + t["in"][0], rel + t["in"][1]
    c, d = rel + t["out"][0], rel + t["out"][1]
    alpha = f"if(lt(t,{b:.3f}),(t-{a:.3f})/{b - a:.3f},if(gt(t,{c:.3f}),({d:.3f}-t)/{d - c:.3f},1))"
    return (f"null[un{n}];{clip}[unart{n}];"
            f"[un{n}][unart{n}]overlay=x={round(x0)}:y={round(y0)}:eof_action=pass,"
            f"drawtext=fontfile={messages.FONT}:textfile={textfile}:expansion=none:fontsize={size}:"
            f"fontcolor={t['color']}:shadowcolor={t['shadow']}:shadowx={shadow}:shadowy={shadow}:"
            f"x={cx:.1f}-tw/2:y={cy:.1f}+{0.36 * size:.1f}-ascent:"
            f"enable='between(t,{a:.3f},{d:.3f})':alpha='{alpha}'")


def _preview(n, rel, spot, pv):
    """The next show's picture, rising and fading in above the bar, playing from `spot`; behind the bar."""
    path, at = spot
    s, length = rel + pv["in"], pv["out"] - pv["in"]
    fade = pv["fade"]
    fades = f"fade=in:st={s:.3f}:d={fade}:alpha=1,"
    if s < 0:  # the segment starts partway through it
        at, length, s, fades = at - s, length + s, 0.0, ""
    if length < 1:
        return "null"
    w, h, b = pv["w"], pv["h"], pv["border"]
    clip = (f"movie={_esc(path)}:seek_point={at:.3f},setpts=PTS-STARTPTS,trim=duration={length:.3f},"
            f"setpts=PTS+{s:.3f}/TB,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
            f"pad={w + 2 * b}:{h + 2 * b}:{b}:{b}:color={pv['color']},format=yuva420p,"
            f"{fades}fade=out:st={s + length - fade:.3f}:d={fade}:alpha=1")
    y = f"{pv['y']}+{pv['rise']}*max(0,1-(t-{s:.3f})/{fade})" if fades else str(pv["y"])
    return (f"null[unv{n}];{clip}[unpv{n}];"
            f"[unv{n}][unpv{n}]overlay=x={pv['x']}:y='{y}':eof_action=pass")


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
    """A path as a filter's option value inside a filtergraph: escaped once for the option (\\ ' :), then
    again for the graph (\\ ' [ ] , ;). Show folders have brackets in them, like "[i_c]" (#103)."""
    path = re.sub(r"([\\':])", r"\\\1", path)
    return re.sub(r"([\\'\[\],;])", r"\\\1", path)
