"""Channel logos burned into a show: the bug a channel like BUZZR or MeTV put in the corner of what it
aired, recorded along with the show. delogo smudges each one out with the picture around it.

A logo is what stays put while everything else moves: each file is looked at once, in the background,
in frames spread through it, for edges that are in the same place in nearly all of them, in a corner.
A file not looked at yet (or whose bars haven't been found yet) plays as it is, and is queued.

Control Room can tell it, for a file or a folder: leave its logos showing, or look harder (one it
missed: in the middle, wider, or there less of the time).
"""

import asyncio
import json
import logging
import os
import subprocess
import threading

from fs42.catalog_api import CatalogAPI
from fs42.station_manager import StationManager
from fs42_live import bars, settings

_l = logging.getLogger("FS42LIVE")

STORE = "runtime/logos.json"  # realpath -> {"size": bytes, "boxes": [[x, y, w, h], ...], "harder": bool}
CHOICES = "runtime/logos_choices.json"  # realpath of a file or folder -> "show" or "harder"
WIDTH = 480  # frames are looked at this wide
FRAMES = 20
STATIC = 0.7  # an edge in this share of the frames that show something is part of a logo
LEAST_SECONDS = 300  # shorter (idents, bumpers, ads) hold too still: all of it would look like a logo
WIDEST = 0.6  # of the picture's width, along the bottom: "www.FuzzyMemories.TV" is half
WIDEST_TOP = 0.3  # at the top a wide one is more often the set (a ceiling the camera holds still on)
HARDER = {"static": 0.5, "least_seconds": 60, "widest": 0.8, "widest_top": 0.8, "corner": False}
CHECK_EVERY = 600
EDGE = bytes([0] + [1] * 255)  # translate: any edge -> 1

_lock = threading.Lock()
_known = None
_queued = set()
_choices = None
_wake = asyncio.Event()


def _load():
    global _known
    if _known is None:
        try:
            with open(STORE) as f:
                _known = json.load(f)
        except FileNotFoundError:
            _known = {}
    return _known


def _save():
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_known, f, indent=0)
    os.replace(tmp, STORE)


def choices():
    global _choices
    if _choices is None:
        try:
            with open(CHOICES) as f:
                _choices = json.load(f)
        except FileNotFoundError:
            _choices = {}
    return _choices


def choice(path):
    """Control Room's choice for a file, its own or its nearest folder's: "show", "harder" or ""."""
    c = choices()
    while path not in c and path not in ("/", ""):
        path = os.path.dirname(path)
    return c.get(path, "")


def choose(path, how):
    """Control Room: a file's or folder's logos left showing ("show"), looked for harder ("harder"), or
    back to the usual (""). What's under it that's chosen on its own follows this."""
    path = os.path.realpath(path)
    with _lock:
        c = choices()
        for p in [p for p in c if p == path or p.startswith(path + "/")]:
            del c[p]
        if how:
            c[path] = how
        tmp = CHOICES + ".tmp"
        with open(tmp, "w") as f:
            json.dump(c, f, indent=0)
        os.replace(tmp, CHOICES)
        if how != "show":  # looked at again, now: not in up to CHECK_EVERY
            _queued.update(p for p in _load() if p == path or p.startswith(path + "/"))
    _wake.set()


def _found(path):
    """The stored boxes if they're for this version of the file, looked for as hard as it's chosen to
    be, else False."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    found = _load().get(path)
    harder = choice(path) == "harder"
    return found["boxes"] if found and found["size"] == size and found.get("harder", False) == harder else False


def logo_filter(path):
    """delogo for each logo in a file, for the very start of the encoder's -vf (the file's own pixels), or ""."""
    if path is None or os.path.splitext(path)[1].lower() not in bars.VIDEO_EXTS or not settings.get("hide_logos"):
        return ""
    path = os.path.realpath(path)
    with _lock:
        if choice(path) == "show":
            return ""
        boxes = _found(path)
        if boxes is False:
            _queued.add(path)
    return "".join(f"delogo=x={x}:y={y}:w={w}:h={h}," for x, y, w, h in boxes or [])


def _frame(path, at, w, h):
    return subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-v", "error", "-ss", f"{at:.1f}", "-i", path, "-frames:v", "1",
         "-an", "-sn", "-dn", "-vf", f"scale={w}:{h},format=gray,edgedetect=low=0.08:high=0.2",
         "-f", "rawvideo", "-"],
        capture_output=True).stdout


def _size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    try:
        vw, vh = map(int, out.strip().split(",")[:2])
        return vw, vh
    except ValueError:
        return 0, 0


def find(path, duration, crop=None, harder=False):
    """[[x, y, w, h], ...] of the logos in a file, in its own pixels (at most one a corner), or []."""
    how = HARDER if harder else {"static": STATIC, "least_seconds": LEAST_SECONDS, "widest": WIDEST,
                               "widest_top": WIDEST_TOP, "corner": True}
    static_share, from_corner = how["static"], how["corner"]

    def fits(bx, by, bw, bh):
        """Not too big for a logo where it is; a wide one is a line of text, not a bar's edge."""
        widest = how["widest"] if by + bh / 2 > y0 + ph / 2 else how["widest_top"]
        return bw <= pw * widest and bh <= ph * 0.25 and (bw <= pw * WIDEST_TOP or bh >= 6)

    if (duration or 0) < how["least_seconds"]:
        return []
    vw, vh = _size(path)
    if not vw or not vh:
        return []
    w, h = WIDTH, max(2, round(vh * WIDTH / vw / 2) * 2)
    # each frame's edges as 0/1 bytes, added up as one big number: a byte per pixel counts its frames
    total, usable = 0, 0
    for k in range(FRAMES):
        f = _frame(path, duration * (0.04 + 0.92 * k / (FRAMES - 1)), w, h)
        if len(f) != w * h:
            continue
        f = f.translate(EDGE)
        if f.count(1) < w * h * 0.01:  # black, or a fade: nothing to tell a logo by
            continue
        total += int.from_bytes(f, "big")
        usable += 1
    if usable < FRAMES / 2:
        return []
    counts = total.to_bytes(w * h, "big")
    need = usable * static_share
    # the picture inside any bars, less a margin: their edges and the frame's are static too
    x0, y0, x1, y1 = (0, 0, w, h) if not crop else (
        crop[2] * w / vw, crop[3] * h / vh, (crop[2] + crop[0]) * w / vw, (crop[3] + crop[1]) * h / vh)
    x0, y0, x1, y1 = int(x0) + 4, int(y0) + 4, int(x1) - 4, int(y1) - 4
    pw, ph = x1 - x0, y1 - y0
    # the top and bottom only: logos sit there, and the middle has scenery that holds still more often
    static = set()
    for y in range(y0, y1):
        if not (y < y0 + ph * 0.3 or y >= y1 - ph * 0.3):
            continue
        for x in range(x0, x1):
            if counts[y * w + x] >= need:
                static.add((x, y))
    # points within 3 px of each other are one shape (a logo's letters); the biggest in each corner
    best, shapes, seen = {}, [], set()
    for p in static:
        if p in seen:
            continue
        seen.add(p)
        todo, group = [p], []
        while todo:
            x, y = todo.pop()
            group.append((x, y))
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    q = (x + dx, y + dy)
                    if q in static and q not in seen:
                        seen.add(q)
                        todo.append(q)
        xs, ys = [q[0] for q in group], [q[1] for q in group]
        bx, by, bw, bh = min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if len(group) >= 6 and bw >= 3 and bh >= 3:  # not a speck, or a line (a bar's edge)
            shapes.append((bx, by, bx + bw, by + bh))
        if len(group) < 15 or bw < 6 or bh < 4 or not fits(bx, by, bw, bh):
            continue
        # from a corner: a web address can run along the bottom from one, but one in the middle is
        # more often part of the set (a game show's desk)
        if from_corner and bx >= x0 + pw * 0.35 and bx + bw <= x1 - pw * 0.35:
            continue
        corner = (bx + bw / 2 > x0 + pw / 2, by + bh / 2 > y0 + ph / 2)
        if corner not in best or len(group) > best[corner][0]:
            best[corner] = (len(group), bx, by, bx + bw, by + bh)
    # with the shapes beside it: a word's letters can be further apart than a logo's ("KISS CARTOON")
    for corner, (n, *box) in best.items():
        grew = True
        while grew:
            grew = False
            for s in shapes:
                if (s[0] - 8 <= box[2] and box[0] - 8 <= s[2] and s[1] - 8 <= box[3] and box[1] - 8 <= s[3]
                        and not (box[0] <= s[0] and box[1] <= s[1] and s[2] <= box[2] and s[3] <= box[3])):
                    grown = [min(box[0], s[0]), min(box[1], s[1]), max(box[2], s[2]), max(box[3], s[3])]
                    if fits(grown[0], grown[1], grown[2] - grown[0], grown[3] - grown[1]):
                        box, grew = grown, True
        best[corner] = (n, box[0], box[1], box[2] - box[0], box[3] - box[1])
    # back to the file's pixels, with room around it: edges only find a logo's outline, and its glow
    # or shadow reaches past that
    sx, sy, boxes = vw / w, vh / h, []
    for _, bx, by, bw, bh in best.values():
        px, py = 3 + bw * 0.2, 3 + bh * 0.2
        x, y = max(1, int((bx - px) * sx)), max(1, int((by - py) * sy))
        boxes.append([x, y, min(int((bw + 2 * px) * sx), vw - x - 1), min(int((bh + 2 * py) * sy), vh - y - 1)])
    return sorted(boxes)


def scan_new_files():
    """Looks at every catalog video (shortest first), skipping files already looked at, and ones whose
    bars aren't known yet. Anything queued meanwhile (by the encoder, or Control Room) goes next."""
    paths = {}
    for station in StationManager().stations:
        if station.get("_has_catalog"):
            for entry in CatalogAPI.get_entries(station):
                path = entry.realpath or os.path.realpath(entry.path)
                if entry.duration and os.path.splitext(path)[1].lower() in bars.VIDEO_EXTS:
                    paths[path] = entry.duration
    order = sorted(paths, key=lambda p: paths[p], reverse=True)  # .pop() takes the shortest
    asked, done = set(), 0
    while True:
        with _lock:
            asked |= _queued
            order += sorted(_queued)
            _queued.clear()
        if not order:
            break
        path = order.pop()
        with _lock:
            if _found(path) is not False or not os.path.exists(path):
                continue
        with bars._lock:
            crop = bars._found(path)
        if crop is False:
            continue
        with _lock:
            harder = choice(path) == "harder"
        boxes = find(path, paths.get(path) or bars._probe_duration(path) or 0, crop, harder)
        with _lock:
            _load()[path] = {"size": os.path.getsize(path), "boxes": boxes, "harder": harder}
            done += 1
            if done % 50 == 0 or path in asked:  # Control Room shows what was asked for as it's done
                _save()
        if boxes:
            _l.info(f"logos: {os.path.basename(path)}: {boxes}")
    if done:
        with _lock:
            _save()
        _l.info(f"logos: looked at {done} files")


async def keep_found():
    while True:
        try:
            await asyncio.to_thread(scan_new_files)
        except Exception:
            _l.exception("logos: scan failed")
        _wake.clear()
        try:  # or sooner, when Control Room asks for a look
            await asyncio.wait_for(_wake.wait(), CHECK_EVERY)
        except asyncio.TimeoutError:
            pass
