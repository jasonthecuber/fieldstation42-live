"""Listens to an episode for the host saying they'll be right back, where the picture never fades
out for the commercials (game shows, mostly). Speech-to-text (faster-whisper, its small English
model, fetched into runtime/models the first time) runs in a process of its own at low priority:

    python -m fs42_live.break_listener EPISODE

prints [[seconds, words], ...]: when each cue's last word ends, and what was said.
"""

import importlib.util
import json
import logging
import os
import re
import subprocess
import sys

MODEL = "base.en"
MODELS = "runtime/models"
THREADS = 2  # half of a 4-core box, so the channels keep playing smoothly
LONGEST = 40 * 60  # seconds; a show this long or longer isn't listened to (movies, dramas)

CUE = re.compile(r"\b(?:(?:we'?ll|i'?ll|we will|i will) be (?:right )?back"
                 r"(?: (?:right )?after (?:this|these|that|the \w+|our \w+))?|right back after|be back right after"
                 r"|right after (?:this|these|the|that|our)|after (?:these|this) (?:messages?|words?|break)"
                 r"|stay (?:tuned|with us|right there)|don'?t go away|when we come back|we'?ll return)\b", re.I)

_l = logging.getLogger("FS42LIVE")


def available():
    return importlib.util.find_spec("faster_whisper") is not None


def listen(path):
    """[(seconds, words)], or None when listening can't be done (it's logged)."""
    try:
        done = subprocess.run(["nice", "-n", "19", sys.executable, "-m", "fs42_live.break_listener", path],
                              capture_output=True, text=True, errors="replace", timeout=3600)
        if done.returncode == 0:
            return [(float(t), words) for t, words in json.loads(done.stdout.strip().splitlines()[-1])]
        _l.warning(f"break finder: can't listen to {path}: {done.stderr.strip().splitlines()[-1:]}")
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as e:
        _l.warning(f"break finder: can't listen to {path}: {e}")
    return None


def cues(segments):
    """[(when the cue's last word ends, the words)] from faster-whisper's segments (with words)."""
    out = []
    for s in segments:
        words = list(s.words or [])
        text = "".join(w.word for w in words) if words else s.text
        found = CUE.search(text)
        if not found:
            continue
        end, upto = s.end, 0
        for w in words:
            upto += len(w.word)
            if upto >= found.end():
                end = w.end
                break
        out.append((round(end, 2), s.text.strip()))
    return out


def main(path):
    from faster_whisper import WhisperModel
    os.makedirs(MODELS, exist_ok=True)
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS, download_root=MODELS)
    segments, _ = model.transcribe(path, language="en", vad_filter=True, beam_size=1, word_timestamps=True)
    print(json.dumps(cues(segments)))


if __name__ == "__main__":
    main(sys.argv[1])
