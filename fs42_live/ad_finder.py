"""Finds the ads recorded into an off-air tape, so the channel skips them.

Some old shows come as recordings of a later rerun on a cable channel, with that channel's ads
(and its promos) recorded right into the video. Played as is, the show stops for ads from the
year it was taped, and then the channel adds its own break after them. Here each such file is
looked at, and the stretches with those ads are stored (play_cuts); the channel plays the show
around them and puts its own breaks where they were (fs42/play_cuts.py, liquid_blocks).

How a tape is read, each file on its own:
  - the cable channel's logo ("bug") sits in a corner of the show and goes away for its ads.
    It's learned from the file itself: the corner detail that holds still in most frames.
  - the rerun's ads were made decades after the show, and sound like it: more deep bass and more
    very high treble than an old studio recording. (A tape that lost its treble: more bass than
    the show.)
  - frames with the bug on are the show; frames with it off and that newer sound are ads. Every
    half second is labeled by the frames it looks most like (nearest neighbours, by the top two
    thirds of the picture), so an ad with quiet sound or a show shot with the bug dropped still
    lands on the right side.
  - runs of ads with only bug-off time between them are one break (but not a moment of the
    show that looked like an ad, just before or after one).
  - each break becomes a cut, from the black (or the quiet) where the show went to break to
    the black where it came back.
Not cut: the cable channel's lead-in at the very start, the show's own squeezed end credits, and
anything with no newer sound in it at all (the show's own prize plugs look like ads too) unless
the bug is off all through a minute or more of it. A break joined on into the end of the show
gives back the joined parts, last first, till it is one of those.

Run by hand on the files that need it; nothing here runs on its own:
    python3 -m fs42_live.ad_finder FILE_OR_FOLDER...          shows what it would cut
    python3 -m fs42_live.ad_finder --go FILE_OR_FOLDER...     stores it and sets the catalog lengths
    python3 -m fs42_live.ad_finder --clear FILE_OR_FOLDER...  the files play whole again
"""

import argparse
import glob
import logging
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing

import numpy as np

sys.path.append(os.getcwd())

from fs42.fluid_builder import FluidBuilder
from fs42.fluid_statements import FluidStatements
from fs42.play_cuts import clean, cut_total

_l = logging.getLogger("FS42LIVE")

W, H = 320, 180  # keyframes, gray, for the bug
FPS = 2  # labels per second
SR = 32000  # audio
SHOW_LIKE = 0.5  # nearest-neighbour share of ads that makes a half second an ad
LOW = 0.25  # an ad run grows over half seconds this ad-like, up to the bug
SHORT_GAP = 8  # seconds between two runs of ads that join anyway
LONG_GAP = 20  # seconds between two runs that join if what's between looks a bit like ads
BUG_OFF_GAP = 60  # seconds between two runs that join if the bug stays off all through it
SURE = 10  # seconds: a run this long is surely ads; a shorter one joins over bug-off time only if it looks it
BUG_OFF_BREAK = 60  # seconds: this long with the bug off all through is a break, newer sound or not
LEAD_IN = 5  # seconds: a run starting this early is the cable channel's lead-in, and stays
LOUDER_IF = 0.1  # with less newer sound than this share of the bug-off time, go by bass instead
SHORTEST = 10  # seconds: a shorter run isn't a break
HEAD = 40  # seconds: the cable channel's lead-in and the show's opening stay
TAIL = 40  # seconds before the end: the squeezed end credits look like ads, and stay
NEWEST = 0.1  # least share of newer sound in a break
TO_BLACK = 5  # seconds after a run starts that the show's own fade to black can still be


def _ffmpeg(args, path):
    return subprocess.run(["nice", "-n", "19", "ffmpeg", "-v", "info", "-hide_banner", "-nostats", "-i", path] + args,
                          capture_output=True, check=True)


def keyframes(path):
    """(gray frames, their times) of the file's keyframes"""
    p = subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-v", "info", "-hide_banner", "-nostats", "-skip_frame", "nokey", "-i", path,
         "-vf", f"scale={W}:{H},format=gray,showinfo", "-vsync", "vfr", "-an", "-f", "rawvideo", "-"],
        capture_output=True, check=True)
    ts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", p.stderr.decode(errors="replace"))]
    f = np.frombuffer(p.stdout, np.uint8).reshape(-1, H, W)
    n = min(len(f), len(ts))
    return f[:n], np.array(ts[:n])


def colors(path):
    """FPS frames a second, 32x18 RGB"""
    raw = _ffmpeg(["-vf", f"fps={FPS},scale=32:18", "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], path).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 18, 32, 3)


def lumas(path):
    """brightness of the middle of each frame, 10 a second"""
    raw = _ffmpeg(["-vf", "fps=10,scale=80:45,format=gray", "-an", "-f", "rawvideo", "-"], path).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, 45, 80)
    return f[:, 5:35, 5:75].mean((1, 2)).astype(np.float32)


def audio(path):
    raw = _ffmpeg(["-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], path).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def _highpass(f):
    f = f.astype(np.float32)
    b = f.copy()
    for ax in (1, 2):  # 5-wide box blur
        b = sum(np.roll(b, s, axis=ax) for s in range(-2, 3)) / 5
    return f - b


def bug_on(frames):
    """Which frames show the corner logo, learned from the frames themselves."""
    hp = _highpass(frames)
    pic = frames.mean(0) > 24
    inner = pic.copy()  # off the picture's edges (pillarbox/letterbox bars)
    for ax in (0, 1):
        for s in range(-4, 5):
            inner &= np.roll(pic, s, axis=ax)
    corner = np.zeros((H, W), bool)
    for ys in (slice(0, H // 3), slice(H - H // 3, H)):
        for xs in (slice(0, W // 3), slice(W - W // 3, W)):
            corner[ys, xs] = True
    sel, sc = hp, None
    for _ in range(3):  # learn, keep the frames that have it, learn again
        if sc is not None:
            sel = hp[sc > (np.percentile(sc, 90) + np.percentile(sc, 10)) / 2]
        cons = np.abs(sel.mean(0)) / (sel.std(0) + 1) * inner * corner
        mask = cons > np.percentile(cons[corner & inner], 99)
        tm = sel.mean(0)[mask]
        tm = (tm - tm.mean()) / (tm.std() + 1e-6)
        v = hp[:, mask]
        v = (v - v.mean(1, keepdims=True)) / (v.std(1, keepdims=True) + 1e-6)
        sc = (v * tm).mean(1)
    return sc > (np.percentile(sc, 90) + np.percentile(sc, 10)) / 2


def sound(x):
    """(loudness every 50 ms in dB, newer-sounding each second, bass share each second in dB)"""
    w = SR // 20
    n = len(x) // w
    db = 10 * np.log10((x[:n * w].reshape(n, w) ** 2).mean(1) + 1e-10)
    freqs = np.fft.rfftfreq(4096, 1 / SR)
    win = np.hanning(4096)
    newer = np.zeros(len(x) // SR, bool)
    bass = np.zeros(len(newer), np.float32)
    for s in range(len(newer)):
        seg = x[s * SR:(s + 1) * SR]
        fr = seg[:len(seg) // 4096 * 4096].reshape(-1, 4096) * win
        P = (np.abs(np.fft.rfft(fr, axis=1)) ** 2).mean(0) + 1e-12
        lo = 10 * np.log10(P[freqs < 120].sum() / P.sum())
        vhi = 10 * np.log10(P[freqs > 11000].sum() / P.sum())
        newer[s] = lo > -19 and vhi > -42
        bass[s] = lo
    return db, newer, bass


def spans(mask, step):
    """[(start, end)] in seconds of the runs of True in mask, one item every `step` s"""
    out, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            out.append((i * step, j * step))
            i = j
        i += 1
    return out


def _feats(a):
    """the picture's look, leaving out its bottom third (the bug, and the box where the cable
    channel told what's on next, over the show)"""
    a = a[:, :12]
    f = a.astype(np.float32) / 255
    n = len(f)
    grid = f.reshape(n, 2, 6, 4, 8, 3).mean((2, 4)).reshape(n, -1)
    q = (a // 64).astype(np.int32)
    idx = q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]
    hist = np.stack([np.bincount(r.ravel(), minlength=64) for r in idx]).astype(np.float32) / (12 * 32)
    return np.concatenate([grid, np.sqrt(hist) * 0.7], 1)


def _knn(X, y, Q, k=7):
    out = np.empty(len(Q), np.float32)
    xx = (X * X).sum(1)
    for s in range(0, len(Q), 500):
        q = Q[s:s + 500]
        d = (q * q).sum(1)[:, None] + xx[None] - 2 * q @ X.T
        out[s:s + 500] = y[np.argpartition(d, k, axis=1)[:, :k]].mean(1)
    return out


def label(on, kt, newer, bass, pics):
    """(how ad-like each half second is, the show for sure, ads by the rules, the bug off) from the
    bug at the keyframes (on, at times kt), the sound each second and the pictures"""
    n = len(pics)
    ft = np.arange(n) / FPS
    p = np.clip(np.searchsorted(kt, ft) - 1, 0, len(kt) - 2)
    show = on[p] & on[p + 1] & (kt[p + 1] - kt[p] < 15)
    off = ~on[p] & ~on[p + 1]
    sec = np.minimum(ft.astype(int), len(newer) - 1)
    ad = off & newer[sec]
    if show.any() and ad.sum() < LOUDER_IF * off.sum():
        # a tape that lost the ads' high treble: the ads are the parts with more bass than the show
        ad = off & (bass[sec] > np.percentile(bass[sec][show], 90))
    known = show | ad
    if not ad.any() or not show.any():
        return np.zeros(n, np.float32), show, ad, off
    X = _feats(pics)
    score = _knn(X[known], ad[known].astype(np.float32), X)
    score[show] = 0
    return np.convolve(score, np.ones(5) / 5, mode="same"), show, ad, off


def breaks(score, show, ad, dur, off=None):
    """[(first, last)] half-second indexes of each break (see the module's notes)"""
    n = len(score)
    off = ~show if off is None else off
    lim = max(0, int((dur - TAIL) * FPS))
    core = score > SHOW_LIKE
    core[lim:] = False
    runs = [[int(a * FPS), int(b * FPS)] for a, b in spans(core, 1 / FPS)]
    for r in runs:  # grow while still ad-like, up to the bug
        while r[0] > 0 and score[r[0] - 1] > LOW and not show[r[0] - 1]:
            r[0] -= 1
        while r[1] < lim and score[r[1]] > LOW and not show[r[1]]:
            r[1] += 1
        if r[1] >= lim:  # into the end credits: end at the least ad-like point of the next 20 s
            w = score[lim:lim + 20 * FPS]
            if len(w):
                r[1] = lim + int(np.argmin(np.convolve(w, np.ones(3) / 3, mode="same")))
    merged = []  # [first, last, [where each joined part ends]]
    for r in runs:
        if merged and r[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], r[1])
            merged[-1][2][-1] = merged[-1][1]
            continue
        if merged and not show[merged[-1][1]:r[0]].any():
            gap = r[0] - merged[-1][1]
            if (gap <= SHORT_GAP * FPS or (gap <= LONG_GAP * FPS and score[merged[-1][1]:r[0]].mean() > 0.2)
                    or _joins_over_bug_off(score, off, merged[-1], r)):
                merged[-1][1] = r[1]
                merged[-1][2].append(r[1])
                continue
        merged.append([r[0], r[1], [r[1]]])

    def newer(i, j):
        return ad[i:j].mean() >= NEWEST

    def bug_off(i, j):
        return min(j, lim) - i >= BUG_OFF_BREAK * FPS and off[i:j].mean() >= 0.95

    out = []
    for i, j, ends in merged:
        for test in (newer, bug_off):  # joined on into the show: give back the joined parts, last first
            if newer(i, j) or bug_off(i, j):
                break
            j = next((e for e in reversed(ends) if test(i, e)), j)
        if (j - i >= SHORTEST * FPS and i / FPS >= LEAD_IN and j / FPS >= HEAD and j <= n
                and (newer(i, j) or bug_off(i, j))):
            out.append((i, j))
    return out


def _joins_over_bug_off(score, off, last, r):
    """does run r join the break before it, over time with the bug off? Not a moment of the show
    that looked like an ad, just before or after the break"""
    gap = r[0] - last[1]
    if gap > BUG_OFF_GAP * FPS or off[last[1]:r[0]].mean() < 0.9:
        return False
    before = last[1] - last[0] >= SURE * FPS or score[last[0]:last[1]].mean() >= SHOW_LIKE
    after = r[1] - r[0] >= SURE * FPS or gap <= SURE * FPS or score[last[1]:r[0]].mean() >= 0.1
    return before and after


def edges(i, j, lim, dark, quiet, dur):
    """(start, end) in seconds of a break, at the black or the quiet where it really starts and ends"""
    s, e = i / FPS, j / FPS
    ds = [a for a, b in dark if s - 2.5 <= a <= s + TO_BLACK]
    qs = [x for x in quiet if s - 2.5 <= x <= s + 1]
    s2 = ds[0] if ds else qs[0] if qs else max(0.0, s - 0.25)
    w0, w1 = (4, 4) if j >= lim else (1, 2.5)  # into the end credits: a few s of credits beats a flash of ad
    de = [b for a, b in dark if e - w0 <= b <= e + w1]
    qe = [x for x in quiet if e - 1 <= x <= e + 2.5]
    e2 = de[-1] if de else qe[-1] if qe else e
    return round(s2, 2), round(min(e2, dur), 2)


def find_cuts(path):
    """[(start, end)] seconds of the recorded ads in a file; [] when it has none"""
    frames, kt = keyframes(path)
    if len(kt) < 20:
        return []
    on = bug_on(frames)
    del frames
    db, newer, bass = sound(audio(path))
    pics = colors(path)
    dur = len(pics) / FPS
    score, show, ad, off = label(on, kt, newer, bass, pics)
    del pics
    found = breaks(score, show, ad, dur, off)
    if not found:
        return []
    dark = [(a, b) for a, b in spans(lumas(path) < 20, 0.1) if b - a >= 0.25]
    quiet = [(a + b) / 2 for a, b in spans(db < -51.0, 0.05)]
    lim = max(0, int((dur - TAIL) * FPS))
    return [edges(i, j, lim, dark, quiet, dur) for i, j in found]


def _files(args):
    out = []
    for a in args:
        if os.path.isdir(a):
            out += sorted(p for p in glob.glob(os.path.join(a, "**", "*"), recursive=True) if os.path.isfile(p))
        else:
            out.append(a)
    return [os.path.realpath(p) for p in out]


def _length(fluid, path):
    cached = fluid.check_file_cache(path)
    if cached and cached.duration:
        return cached.duration
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                capture_output=True, text=True).stdout or 0)


def set_lengths(fluid, path, whole):
    """Each catalog entry for the file plays as long as it does now, where its length was the
    whole file's, where it ends (end_finder) or where it ends less what was cut before."""
    from fs42.catalog_io import CatalogIO
    end = fluid.get_play_end(path, whole) or whole
    plays = fluid.get_play_length(path, whole)
    n = 0
    with CatalogIO()._get_connection() as db:
        rows = db.execute("SELECT id, path, realpath, duration FROM catalog_entries").fetchall()
        for rid, p, real, d in rows:
            if (real or os.path.realpath(p)) != path or abs(d - plays) < 0.01:
                continue
            if abs(d - whole) < 1 or abs(d - end) < 1 or d < end:
                db.execute("UPDATE catalog_entries SET duration = ? WHERE id = ?", (plays, rid))
                n += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--go", action="store_true", help="store the cuts and set the catalog lengths")
    ap.add_argument("--clear", action="store_true", help="play the files whole again")
    a = ap.parse_args(argv)
    fluid = FluidBuilder()
    with closing(sqlite3.connect(fluid.db_path)) as db:
        FluidStatements.init_db(db)
    for path in _files(a.paths):
        whole = _length(fluid, path)
        name = os.path.basename(path)
        if a.clear:
            with closing(sqlite3.connect(fluid.db_path)) as db:
                db.execute("DELETE FROM play_cuts WHERE path = ?", (path,))
                db.commit()
            print(f"{name}: plays whole; {set_lengths(fluid, path, whole)} catalog entries", flush=True)
            continue
        cuts = clean(find_cuts(path), whole)
        total = cut_total(cuts, whole)
        print(f"{name}: {len(cuts)} breaks, {total:.0f} s: " +
              ", ".join(f"{s:.2f}-{e:.2f}" for s, e in cuts), flush=True)
        if a.go:
            with closing(sqlite3.connect(fluid.db_path)) as db:
                FluidStatements.add_play_cuts(db, path, cuts, whole)
            print(f"  stored; {set_lengths(fluid, path, whole)} catalog entries", flush=True)


if __name__ == "__main__":
    main()
