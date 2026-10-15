"""Imports a compilation of old commercials from archive.org as single spots for a station's breaks.

Run inside the fs42-live container:
  docker exec fs42-live python3 fs42_live/tools/import_commercials.py \
      catalog/miami_vice/commercial 1981-commercial-breaks ["part of a file name" ...]

Downloads the item's videos (only those whose names contain one of the given parts, if any),
finds the cuts between spots by the short black gaps between them, and saves each spot as a
640x480 mp4 with even volume in <dest>/<item>/. Already-imported files are skipped, so it can
be rerun. Rebuild the station's catalog afterwards.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

VIDEO = (".mp4", ".mkv", ".avi", ".mpg", ".mpeg", ".mov", ".m4v", ".ogv", ".webm")
SPOT_LENGTHS = (10, 15, 20, 30, 45, 60, 90, 120)  # seconds; what spots ran at
SNAP = 1.5  # how far a cut may be from a standard length and still count as one
SHORTEST, LONGEST = 9, 125  # anything else between two cuts isn't a spot
LOUDNESS = -22  # LUFS; a touch above the shows (Miami Vice is about -23.5), as real breaks were
DOWNLOADS = "runtime/commercial_downloads"


def main(dest, item, parts):
    os.nice(10)  # stay out of the way of live channels
    for name in item_videos(item, parts):
        out_dir = os.path.join(dest, item)
        stem = re.sub(r"\W+", "_", os.path.splitext(name)[0]).strip("_")
        if os.path.isdir(out_dir) and any(f.startswith(stem + "_") for f in os.listdir(out_dir)):
            print(f"{name}: already imported")
            continue
        source = download(item, name)
        spots = find_spots(source)
        print(f"{name}: {len(spots)} spots, {sum(e - s for s, e in spots) / 60:.0f} of "
              f"{duration(source) / 60:.0f} min")
        os.makedirs(out_dir, exist_ok=True)
        crop = find_crop(source)
        for n, (start, end) in enumerate(spots, 1):
            save_spot(source, start, end, crop, os.path.join(out_dir, f"{stem}_{n:03}.mp4"))
        os.remove(source)
    shutil.rmtree(os.path.join(DOWNLOADS, item), ignore_errors=True)


def item_videos(item, parts):
    with urllib.request.urlopen(f"https://archive.org/metadata/{item}") as r:
        files = json.load(r).get("files", [])
    names = [f["name"] for f in files if f.get("source") == "original" and f["name"].lower().endswith(VIDEO)]
    if parts:
        names = [n for n in names if any(p.lower() in n.lower() for p in parts)]
    if not names:
        sys.exit(f"no matching videos in archive.org item {item}")
    return sorted(names)


def download(item, name):
    path = os.path.join(DOWNLOADS, item, name)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        url = f"https://archive.org/download/{item}/{urllib.parse.quote(name)}"
        print(f"downloading {name}")
        for attempt in range(4):  # archive.org has the odd server error
            try:
                with urllib.request.urlopen(url) as r, open(path + ".part", "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                break
            except urllib.error.URLError as e:
                if attempt == 3:
                    raise
                print(f"  {e}; trying again")
                time.sleep(30 * (attempt + 1))
        os.rename(path + ".part", path)
    return path


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def find_spots(path):
    # spots are separated by a few black frames; cut in the middle of each black stretch
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-an", "-vf",
                          "scale=160:-2,blackdetect=d=0.05:pic_th=0.92:pix_th=0.12", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    cuts = [(float(s) + float(e)) / 2 for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
    cuts.append(duration(path))

    # a fade inside a spot is black too, so prefer the cut that makes a standard-length spot
    spots, start = [], 0.0
    while True:
        later = [c for c in cuts if c > start + SHORTEST - SNAP]
        if not later:
            return spots
        standard = [c for c in later if any(abs(c - start - n) <= SNAP for n in SPOT_LENGTHS)]
        end = standard[0] if standard else later[0]
        if SHORTEST - SNAP <= end - start <= LONGEST:
            spots.append((start, end))
        start = end


def find_crop(path):
    # many uploads are 4:3 video inside a 16:9 frame; trim the bars so it fills the screen
    middle = duration(path) / 2
    log = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(middle), "-i", path, "-t", "30", "-an",
                          "-vf", "cropdetect=limit=0.1:round=2:reset=0", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    found = re.findall(r"crop=(\d+:\d+:\d+:\d+)", log)
    return found[-1] if found else None


def save_spot(source, start, end, crop, out):
    # trim a few frames off each end so no black or the neighbouring spot shows
    start, end = start + 0.1, end - 0.1
    vf = (f"crop={crop}," if crop else "") + "scale=640:480,setsar=1"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", source, "-t", f"{end - start:.3f}",
                    "-vf", vf, "-af", f"loudnorm=I={LOUDNESS}:TP=-2:LRA=11,aresample=48000", "-ac", "2",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-threads", "2",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out + ".part.mp4"], check=True)
    os.rename(out + ".part.mp4", out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
