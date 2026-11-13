"""Where a show's breaks usually fall, for its episodes the break finder found nothing in.

A show's breaks land in nearly the same places from one episode to the next: the one after the
cold open and the one before the tag within seconds, the middle one within a minute or two. So
from the episodes with clear breaks (black and quiet at once), each break gets a window: where
it falls in the middle half of them, with a minute's slack. An episode with nothing clear gets a
closer look only inside those windows, where a weaker sign will do (a short or grey fade, or a
scene cut in a hush), since a break is known to be near. A window with nothing in it takes the
show's usual time.
"""

import re
import statistics
import subprocess
from collections import defaultdict

FEWEST_EPISODES = 5  # with clear breaks, before a show's pattern is trusted...
MOST_EPISODES_LIKE_IT = 0.6  # ...and this share of them must have the usual number of breaks
WIDEST = 300  # seconds; a break moving more than this (its middle half) isn't a pattern
SLACK = 60  # seconds either side of where a break's middle half falls
SAME_LENGTH = 0.1  # an episode this much longer or shorter than usual is cut differently
EDGE = 20  # seconds from either end, as in break_finder
CLOSEST = 120  # seconds; two breaks can't be closer than this


def show_of(path):
    """The show's folder: the one right under a library section (library/TV/Cheers (1982)) or a
    channel's folder (channels/gameshows/Family Feud). A file right in a channel's folder is its
    own show; elsewhere, the folder it's in."""
    parts = path.split("/")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] in ("library", "channels") and len(parts) > i + 3:
            return "/".join(parts[:i + 3])
        if parts[i] == "channels" and len(parts) == i + 3:
            return path
    return "/".join(parts[:-1])


class Pattern:
    def __init__(self, breaks, length):
        self.breaks = breaks  # [(from_end, low, usual, high)], seconds from the start or the end
        self.length = length

    def fits(self, duration):
        return abs(duration - self.length) <= self.length * SAME_LENGTH

    def windows(self, duration):
        """[(low, usual, high)] in this episode's seconds."""
        out = []
        for from_end, low, usual, high in self.breaks:
            if from_end:
                low, usual, high = duration - high, duration - usual, duration - low
            low, high = max(low, EDGE), min(high, duration - EDGE)
            if low < high:
                out.append((low, min(max(usual, low), high), high))
        return out


def learn(stored, unsure=()):
    """{show: Pattern} from the stored breaks ({path: segments}), leaving out the paths in unsure
    (those placed by a pattern themselves, so a guess doesn't teach the next one)."""
    found = defaultdict(list)
    for path, segments in stored.items():
        if segments and path not in unsure:
            found[show_of(path)].append(([s["chapter_end"] for s in segments[:-1]], segments[-1]["chapter_end"]))
    patterns = {}
    for show, episodes in found.items():
        if len(episodes) < FEWEST_EPISODES:
            continue
        count = statistics.mode(len(b) for b, _ in episodes)
        alike = [(b, d) for b, d in episodes if len(b) == count]
        if not count or len(alike) < max(FEWEST_EPISODES, MOST_EPISODES_LIKE_IT * len(episodes)):
            continue
        length = statistics.median(d for _, d in alike)
        breaks = []
        for k in range(count):
            from_end = statistics.median(b[k] for b, _ in alike) > length / 2
            at = sorted(d - b[k] if from_end else b[k] for b, d in alike)
            q1, usual, q3 = statistics.quantiles(at, n=4)
            if q3 - q1 > WIDEST:
                break
            breaks.append((from_end, q1 - SLACK, usual, q3 + SLACK))
        else:
            patterns[show] = Pattern(breaks, length)
    return patterns


def look_closer(path, duration, pattern):
    """(points, "closer" if any were seen, else "usual")."""
    points, seen = [], False
    for low, usual, high in pattern.windows(duration):
        at = _best_in(path, low, high, usual)
        seen |= at is not None
        points.append(usual if at is None else at)
    return _apart(sorted(points)), "closer" if seen else "usual"


def _best_in(path, low, high, usual):
    log = subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{low:.2f}", "-t", f"{high - low:.2f}",
         "-i", path, "-vf", "blackdetect=d=0.1:pix_th=0.15:pic_th=0.9,select=gt(scene\\,0.3),metadata=print",
         "-af", "silencedetect=n=-35dB:d=0.2", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace", check=True).stderr
    blacks = [(float(s), float(e)) for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
    starts = [float(s) for s in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(e) for e in re.findall(r"silence_end: ([\d.]+)", log)] + [high - low]
    hushes = list(zip(starts, ends))
    cuts = [float(t) for t in re.findall(r"Parsed_metadata.*pts_time:([\d.]+)", log)]
    for found in (_black_and_quiet(blacks, hushes), _cut_in_hush(cuts, hushes), [(a + b) / 2 for a, b in blacks]):
        if found:
            return low + min(found, key=lambda t: abs(low + t - usual))
    return None


def _black_and_quiet(blacks, hushes):
    out = []
    for b0, b1 in blacks:
        for s0, s1 in hushes:
            lo, hi = max(b0, s0), min(b1, s1)
            if hi - lo >= 0.1:
                out.append((lo + hi) / 2)
                break
    return out


def _cut_in_hush(cuts, hushes):
    return [t for t in cuts if any(s0 - 0.2 <= t <= s1 + 0.2 and s1 - s0 >= 0.4 for s0, s1 in hushes)]


def _apart(points):
    out = []
    for p in points:
        if not out or p - out[-1] >= CLOSEST:
            out.append(p)
    return out
