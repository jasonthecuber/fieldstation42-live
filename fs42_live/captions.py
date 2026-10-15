"""MTV-style captions on music videos: artist, "title" and year in the lower left, shown a few
seconds into each video and again shortly before it ends.

The text comes from the file name, "Artist - Title (1985).ext"; tags like "(Official Video)"
are dropped. A station turns captions on for its clip folders with `live_captions`, e.g.
"live_captions": ["music_videos"].
"""

import hashlib
import os
import re

ROOT = "/dev/shm/fs42_captions"
FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
SHOW_AT = 4.0  # seconds into the video
SHOW_FOR = 7.0
END_AT = 15.0  # seconds before the end it shows again
FADE = 0.5
JUNK = re.compile(r"\s*[(\[][^)\]]*\b(official|video|remaster\w*|hq|hd|audio|lyrics?)\b[^)\]]*[)\]]", re.I)
YEAR = re.compile(r"\s*\((\d{4})\)$")


def wanted(station, path):
    return any(f"/{tag}/" in path for tag in station.get("live_captions", []))


def parse(path):
    """(artist, title, year) from a file name; artist and year are None when it has none."""
    name = JUNK.sub("", os.path.splitext(os.path.basename(path))[0]).strip()
    year = None
    if m := YEAR.search(name):
        year, name = m.group(1), name[:m.start()].strip()
    artist, dash, title = name.partition(" - ")
    if not dash:
        return None, name, year
    return artist.strip(), title.strip(), year


def drawtext(path, start, end):
    """The filter for a segment playing a video from `start` to `end` seconds into it."""
    artist, title, year = parse(path)
    text = "\n".join(line for line in (artist, f'"{title}"' if artist else title, year) if line)
    os.makedirs(ROOT, exist_ok=True)
    # from a file, so quotes and colons in titles need no filter escaping
    textfile = os.path.join(ROOT, hashlib.sha1(text.encode()).hexdigest()[:16] + ".txt")
    if not os.path.exists(textfile):
        with open(textfile, "w") as f:
            f.write(text)

    at = f"(t+{start:.3f})"  # time in the video, not the segment
    windows = [(SHOW_AT, SHOW_AT + SHOW_FOR)]
    if end - END_AT > SHOW_AT + SHOW_FOR:
        windows.append((end - END_AT, end - END_AT + SHOW_FOR))
    enable = "+".join(f"between({at},{a:.3f},{b:.3f})" for a, b in windows)
    fades = [f"clip(min({at}-{a:.3f},{b:.3f}-{at})/{FADE},0,1)" for a, b in windows]
    alpha = fades[0] if len(fades) == 1 else f"max({fades[0]},{fades[1]})"
    return (f"drawtext=fontfile={FONT}:textfile={textfile}:expansion=none:fontsize=20:fontcolor=white:"
            f"line_spacing=6:borderw=1:bordercolor=black@0.6:shadowcolor=black@0.8:shadowx=2:shadowy=2:"
            f"x=56:y=h-th-56:enable='{enable}':alpha='{alpha}'")
