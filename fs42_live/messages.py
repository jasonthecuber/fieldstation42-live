"""Messages put on the air for a while: a crawl along the bottom of a channel (or all of them),
and the greeting on the greeting channel. Each one ends on its own after its minutes are up.

Set from the live page, or `python -m fs42_live.tools.say` in the container. They're kept in
runtime/messages.json, so a restart doesn't lose them.
"""

import hashlib
import json
import os
import threading
import time

STORE = "runtime/messages.json"
ROOT = "/dev/shm/fs42_messages"
FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
SPEED = 90  # pixels a second the crawl moves
GAP = "          "  # between one pass of the crawl and the next
DEFAULT_MINUTES = 30

_lock = threading.Lock()


def _load():
    try:
        with open(STORE) as f:
            saved = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    now = time.time()
    return {k: m for k, m in saved.items() if m["until"] > now}


def _save(messages):
    with open(STORE + ".tmp", "w") as f:
        json.dump(messages, f, indent=1)
    os.replace(STORE + ".tmp", STORE)


def current():
    """What's on the air now: {"crawl:12": {...}, "crawl:all": {...}, "greeting": {...}}."""
    with _lock:
        return _load()


def put(key, text, minutes=DEFAULT_MINUTES, **extra):
    """Put a message on the air (key "crawl:<channel>", "crawl:all" or "greeting"), or take it off
    with no text."""
    with _lock:
        messages = _load()
        if text.strip():
            messages[key] = {"text": text.strip()[:300], "until": time.time() + max(1, minutes) * 60, **extra}
        else:
            messages.pop(key, None)
        _save(messages)
        return messages


def crawl(channel):
    """(text, seconds left) of the crawl for a channel (its own, else the one for all), or None."""
    messages = current()
    m = messages.get(f"crawl:{channel}") or messages.get("crawl:all")
    return (m["text"], m["until"] - time.time()) if m else None


def crawl_filter(channel, offset=0.0):
    """The drawtext filter for a channel's crawl, or None. `offset` is where this encode starts in the
    stream, so the crawl carries on smoothly from one encode to the next instead of starting over."""
    found = crawl(channel)
    if not found:
        return None
    text, left = found
    os.makedirs(ROOT, exist_ok=True)
    # from a file, so quotes and colons need no filter escaping
    textfile = os.path.join(ROOT, hashlib.sha1(text.encode()).hexdigest()[:16] + ".txt")
    if not os.path.exists(textfile):
        with open(textfile, "w") as f:
            f.write(text + GAP)
    at = f"(t+{offset:.3f})"
    return (f"drawtext=fontfile={FONT}:textfile={textfile}:expansion=none:fontsize=30:fontcolor=yellow:"
            f"box=1:boxcolor=black@0.65:boxborderw=10:borderw=2:bordercolor=black:"
            f"x='w-mod({at}*{SPEED},w+tw)':y=h-th-34:enable='lt(t,{left:.1f})'")


def join(*filters):
    return ",".join(f for f in filters if f) or None
