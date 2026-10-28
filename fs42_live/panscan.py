"""Pan and scan: a wide movie cropped to a narrower screen (a viewer's Full screen, or a channel's 16:9 for
a 2.35 film) follows the action, as TV's operators did, instead of always showing the middle.

Each wide file is looked at once, in the background, in its own low-priority process (about a ninth of
the movie's length, with the GPU decoding): every frame for cuts, every 4th for faces (YuNet). In each shot,
its people are the faces there most of the time and about as big as the biggest; passers-by don't count.
The window wants all of them if they fit, else whoever's mouth is moving, else the biggest. An operator
then follows that: it holds still while they stay near the middle, then pans (or cuts, if it's far), and
waits a moment between moves. What's stored is that: where the window's middle is, shot by shot.

It's planned for a 4:3 window; a wider one (14:9, 16:9) follows the same middle. A file not looked at yet
plays with the middle, as before, and goes next. Control Room can keep a file or folder in the middle.
"""

import asyncio
import bisect
import itertools
import json
import logging
import os
import statistics
import subprocess
import sys
import tempfile
import threading

from fs42.catalog_api import CatalogAPI
from fs42.station_manager import StationManager
from fs42_live import bars, hwaccel, settings

_l = logging.getLogger("FS42LIVE")

STORE = "runtime/panscan.json"  # realpath -> {"size": bytes, "fps": n, "shots": [[start s, middle, [[from s, to s, middle], ...]], ...] or None}
CHOICES = "runtime/panscan_choices.json"  # realpath of a file or folder -> "middle"
MODEL = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx")
WIDE = 1.45  # wider than this (4:3 is 1.33) is worth following
LEAST_SECONDS = 600  # trailers and ads aren't
CHECK_EVERY = 600

# looking (in the worker)
AW = 640  # frames are looked at this wide
EVERY = 4  # faces in every 4th frame; cuts in every one
CUT = 20  # a cut: the picture changes this much at once (mean of a 32x18 thumbnail, 0-255)
WINDOW = 4 / 3

# the operator
HOLD_OFF = 0.18  # of the window: how far the subject strays before the window follows
AWAY = 0.5  # seconds it must be gone first; the move then starts when it went (less LEAD)
LEAD = 0.2
CUT_OVER = 0.5  # of the window: farther than this, cut to it
PAN_SPEED = 0.35  # windows a second
PAN_SHORTEST, PAN_LONGEST = 0.8, 2.0  # seconds
MIN_HOLD = 1.5  # seconds between moves

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
        json.dump(_known, f, separators=(",", ":"))
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
    """Control Room's choice for a file, its own or its nearest folder's: "middle" or ""."""
    c = choices()
    while path not in c and path not in ("/", ""):
        path = os.path.dirname(path)
    return c.get(path, "")


def choose(path, how):
    """Control Room: a file or folder kept in the middle ("middle") or following the action again (""). What's
    under it that's chosen on its own follows this."""
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


def _found(path):
    """The stored plan if it's for this version of the file ({"shots": None} if it's not wide), else False."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    found = _load().get(path)
    return found if found and found["size"] == size else False


def _middle(plan, t):
    """Where the window's middle is, as a share of the picture's width, at t seconds into the file."""
    shots = plan["shots"]
    i = bisect.bisect_right([s[0] for s in shots], t) - 1
    if i < 0:
        return 0.5
    x = shots[i][1]
    for m0, m1, to in shots[i][2]:
        if t >= m1:
            x = to
        elif t >= m0:
            u = (t - m0) / max(1e-3, m1 - m0)
            return x + (to - x) * u * u * (3 - 2 * u)
    return x


def pan_filter(path, start, duration):
    """For a file cropped narrower than it is: (a sendcmd that moves the crop named "crop@pan" from where it
    is at `start` through the next `duration` seconds, its commands file), or ("", None). The caller
    deletes the file once ffmpeg is done."""
    if path is None or os.path.splitext(path)[1].lower() not in bars.VIDEO_EXTS or not settings.get("pan_scan"):
        return "", None
    path = os.path.realpath(path)
    with _lock:
        if choice(path) == "middle":
            return "", None
        plan = _found(path)
        if plan is False:
            _queued.add(path)
    if not plan or not plan["shots"]:
        return "", None
    step = 1 / plan["fps"]
    starts = [s[0] for s in plan["shots"]]
    moves = [(m0, m1) for s in plan["shots"] for m0, m1, _ in s[2]]
    times = {start} | {t for t in starts if start < t < start + duration}
    for m0, m1 in moves:
        t = max(m0, start)
        while t < min(m1 + step, start + duration):
            times.add(t)
            t += step
    lines, last = [], None
    for t in sorted(times):
        x = round(_middle(plan, t), 4)
        if x != last:
            # half a frame early: the times are rounded, and a cut's move a frame late shows the old place
            lines.append(f"{max(0.0, t - start - step / 2):.3f} crop@pan x iw*{x}-ow/2;\n")
            last = x
    fd, name = tempfile.mkstemp(prefix="fs42-pan-", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.writelines(lines)
    return f"sendcmd=f={name},", name


def _probe(path):
    """(width, height, pixel aspect, frames a second) of a file's video, or None."""
    try:
        o = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,sample_aspect_ratio,r_frame_rate", "-of", "json", path],
            capture_output=True, text=True, timeout=60).stdout)["streams"][0]
        a, b = o["r_frame_rate"].split("/")
        sar = o.get("sample_aspect_ratio", "1:1").split(":")
        sar = float(sar[0]) / float(sar[1]) if len(sar) == 2 and float(sar[1]) and float(sar[0]) else 1.0
        return o["width"], o["height"], sar, float(a) / float(b)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError, ZeroDivisionError):
        return None


def look(path, crop):
    """The worker: a file's plan (see STORE), with its bars `crop` ([w, h, x, y], or None) cut off first."""
    import cv2
    import numpy as np

    vw, vh, sar, fps = _probe(path)
    fh = round(vh * AW / (vw * sar) / 2) * 2  # the whole frame, square pixels, AW wide
    cw, ch, cx, cy = crop or (vw, vh, 0, 0)
    pw, ph = round(cw * AW / vw / 2) * 2, round(ch * fh / vh / 2) * 2
    px, py = min(round(cx * AW / vw), AW - pw), min(round(cy * fh / vh), fh - ph)
    ww = ph * WINDOW
    if pw <= ww:
        return None
    det = cv2.FaceDetectorYN.create(MODEL, "", (pw, ph), 0.6)

    def diff(g1, g0, x0, y0, x1, y1):
        x0, y0, x1, y1 = (int(max(0, v)) for v in (x0, y0, x1, y1))
        x1, y1 = min(x1, pw), min(y1, ph)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return 0.0
        return float(np.abs(g1[y0:y1, x0:x1] - g0[y0:y1, x0:x1]).mean())

    def frames(gpu):
        # the GPU decodes and shrinks it, if it can (a sixth of the work), else the CPU
        dec = ["-hwaccel", "vaapi", "-hwaccel_device", hwaccel.DEVICE, "-hwaccel_output_format", "vaapi"] if gpu else []
        scale = f"scale_vaapi=w={AW}:h={fh},hwdownload,format=nv12," if gpu else f"scale={AW}:{fh},"
        p = subprocess.Popen(
            ["ffmpeg", "-v", "error", *dec, "-i", path, "-an", "-sn", "-dn", "-vf",
             f"{scale}crop={pw}:{ph}:{px}:{py},fps={fps},format=bgr24", "-f", "rawvideo", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        while len(buf := p.stdout.read(pw * ph * 3)) == pw * ph * 3:
            yield np.frombuffer(buf, np.uint8).reshape(ph, pw, 3)
        p.wait()

    gpu = hwaccel.vaapi()
    stream = frames(gpu)
    first = next(stream, None)
    if first is None and gpu:  # a codec the GPU can't decode
        stream = frames(False)
        first = next(stream, None)
    cuts = [0]  # frames where a shot starts
    looks = []  # (frame, [(middle x, width, mouth moving)])
    prev_thumb, last_gray, last_look, n = None, None, -99, 0
    for img in itertools.chain([first] if first is not None else [], stream):
        thumb = cv2.resize(img, (32, 18), interpolation=cv2.INTER_AREA).astype(np.int16)
        if prev_thumb is not None and np.abs(thumb - prev_thumb).mean() > CUT:
            cuts.append(n)
        prev_thumb = thumb
        if n - last_look >= EVERY or cuts[-1] == n:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.int16)
            same_shot = last_gray is not None and last_look >= cuts[-1]
            faces = []
            found = det.detect(img)[1]
            for f in found if found is not None else []:
                x, y, w, h = f[:4]
                act = 0.0
                if same_shot:
                    # the mouth's change, less the eyes' (the head moving, or the camera)
                    mcx, mcy, mw = (f[10] + f[12]) / 2, (f[11] + f[13]) / 2, abs(f[12] - f[10])
                    mouth = diff(gray, last_gray, mcx - mw * 0.8, mcy - h * 0.12, mcx + mw * 0.8, mcy + h * 0.18)
                    eyes = diff(gray, last_gray, x, f[5] - h * 0.1, x + w, f[5] + h * 0.1)
                    act = max(0.0, mouth - eyes)
                faces.append((float(x + w / 2), float(w), act))
            looks.append((n, faces))
            last_gray, last_look = gray, n
        n += 1
    cuts.append(n)

    # the shot's own people: faces followed through it that are there most of the time and about as big
    # as the biggest
    kept = []
    for s0, s1 in zip(cuts, cuts[1:]):
        shot = [(fr, faces) for fr, faces in looks if s0 <= fr < s1]
        tracks, marks = [], []  # [last x, last width, looks seen in, widths]
        for fr, faces in shot:
            ids = []
            for c, w, _ in faces:
                best = min((abs(tr[0] - c), i) for i, tr in enumerate(tracks)) if tracks else (9e9, -1)
                if tracks and best[0] < max(w, tracks[best[1]][1]) * 0.75 and best[1] not in ids:
                    i = best[1]
                else:
                    tracks.append([c, w, 0, []])
                    i = len(tracks) - 1
                tracks[i][0], tracks[i][1] = c, w
                tracks[i][2] += 1
                tracks[i][3].append(w)
                ids.append(i)
            marks.append(ids)
        biggest = max((statistics.median(tr[3]) for tr in tracks), default=0)
        main = {i for i, tr in enumerate(tracks)
                if tr[2] >= max(1, 0.4 * len(shot)) and statistics.median(tr[3]) >= biggest * 0.5}
        for (fr, faces), ids in zip(shot, marks):
            kept.append((fr, [f for f, i in zip(faces, ids) if i in main]))
    looks = kept

    # where each look wants the window: everyone if they fit, else whoever is talking, else the biggest
    targets = []
    for k, (fr, faces) in enumerate(looks):
        target = None
        if faces:
            left = min(c - w / 2 for c, w, _ in faces)
            right = max(c + w / 2 for c, w, _ in faces)
            if right - left <= ww * 0.85:
                target = (left + right) / 2
            else:
                near = looks[max(0, k - 6):k + 4]
                scored = sorted(((sum(a for _, fs in near for c2, _, a in fs if abs(c2 - c) < w), c)
                                 for c, w, _ in faces), reverse=True)
                target = scored[0][1] if scored[0][0] > 2 else max(faces, key=lambda f: f[1])[0]
        targets.append((fr, target))

    def clamp(x):
        return max(ww / 2, min(pw - ww / 2, x))

    shots = []
    for s0, s1 in zip(cuts, cuts[1:]):
        if s1 <= s0:
            continue
        ts = [(fr, x) for fr, x in targets if s0 <= fr < s1]
        # each look's target, steadied: the median of those within half a second either side
        steady = []
        for fr, x in ts:
            around = [x2 for fr2, x2 in ts if abs(fr2 - fr) <= fps / 2 and x2 is not None]
            steady.append((fr, statistics.median(around) if around else None))
        known = [x for _, x in steady if x is not None]
        pos = start = clamp(statistics.median(known[:6]) if known else pw / 2)
        moves, away_since, free_at = [], None, s0
        for fr, x in steady:
            if x is None or fr < free_at:
                continue
            x = clamp(x)
            if abs(x - pos) <= ww * HOLD_OFF:
                away_since = None
                continue
            away_since = away_since if away_since is not None else fr
            if fr - away_since < fps * AWAY:
                continue
            d = abs(x - pos)
            if d > ww * CUT_OVER:
                begin = end = fr
            else:
                begin = max(away_since - int(fps * LEAD), free_at)
                end = min(begin + int(fps * min(PAN_LONGEST, max(PAN_SHORTEST, d / (ww * PAN_SPEED)))), s1 - 1)
            moves.append([round(begin / fps, 3), round(end / fps, 3), round(x / pw, 4)])
            pos, away_since, free_at = x, None, end + int(fps * MIN_HOLD)
        if moves or not shots or round(start / pw, 4) != shots[-1][1] or shots[-1][2]:
            shots.append([round(s0 / fps, 3), round(start / pw, 4), moves])
    return {"fps": fps, "shots": shots}


def _wide(path, crop):
    """Whether a file's picture, inside its bars, is wider than WIDE."""
    probed = _probe(path)
    if not probed:
        return False
    vw, vh, sar, _ = probed
    w, h = (crop[0], crop[1]) if crop else (vw, vh)
    return h > 0 and w * sar / h > WIDE


def scan_new_files():
    """Looks at each long catalog video not looked at yet: the ones on channels that crop first, shortest
    first. Anything queued meanwhile (a viewer zoomed in on it) goes next."""
    paths = {}
    for station in StationManager().stations:
        if station.get("_has_catalog"):
            first = 0 if station.get("live_crop_to") else 1
            for entry in CatalogAPI.get_entries(station):
                path = entry.realpath or os.path.realpath(entry.path)
                if (entry.duration or 0) >= LEAST_SECONDS and os.path.splitext(path)[1].lower() in bars.VIDEO_EXTS:
                    paths[path] = min(paths.get(path, (9, 0)), (first, entry.duration))
    order = sorted(paths, key=lambda p: paths[p], reverse=True)  # .pop() takes the first
    done = 0
    while True:
        with _lock:
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
        plan = {"fps": 0, "shots": None}
        if _wide(path, crop):
            run = subprocess.run(["nice", "-n", "19", sys.executable, "-m", "fs42_live.panscan", path, json.dumps(crop)],
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True)
            if run.returncode != 0:
                _l.warning(f"panscan: {os.path.basename(path)}: {run.stderr.strip()[-300:]}")
                continue
            plan = json.loads(run.stdout) or plan
            _l.info(f"panscan: {os.path.basename(path)}: {len(plan['shots'] or [])} shots")
        with _lock:
            _load()[path] = {"size": os.path.getsize(path), **plan}
            done += 1
            if plan["shots"] or done % 50 == 0:
                _save()
    if done:
        with _lock:
            _save()
        _l.info(f"panscan: looked at {done} files")


async def keep_found():
    while True:
        try:
            await asyncio.to_thread(scan_new_files)
        except Exception:
            _l.exception("panscan: scan failed")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), CHECK_EVERY)
        except asyncio.TimeoutError:
            pass


if __name__ == "__main__":
    print(json.dumps(look(sys.argv[1], json.loads(sys.argv[2]))))
