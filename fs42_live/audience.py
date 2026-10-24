"""Who's watching what, for the box's Control Room: a stream counts while it's open, an HLS
player (Safari) while it keeps fetching segments. Jellyfin's Live TV shows up here too, as
streams from Jellyfin's address; Control Room lists those from Jellyfin's side instead."""

import itertools
import time

HLS_GONE = 20  # seconds without a segment fetch before an HLS player counts as gone
# A stream sends all the time (live, and on demand in real time; a paused on demand player stops
# its stream after 20 s), so a viewer that has taken nothing for this long is gone: a TV switched
# off mid-stream never says so, and its stream stays open until TCP gives up.
STALLED = 30

_open = {}  # key -> entry
_hls = {}   # (host, agent) -> entry, with "last"
_keys = itertools.count()


def _entry(request, what):
    return {"host": request.client.host if request.client else "", "agent": request.headers.get("user-agent", ""),
            "since": time.time(), **what}


async def counted(request, what, stream):
    """`stream`, with its viewer listed for as long as it's open."""
    key = next(_keys)
    entry = _open[key] = _entry(request, what)
    try:
        async for chunk in stream:
            entry["taken"] = time.time()
            yield chunk
    finally:
        _open.pop(key, None)


def fetched(request, what):
    """An HLS segment was fetched."""
    entry = _entry(request, what)
    kept = _hls.get((entry["host"], entry["agent"]))
    if kept and kept["channel"] == what["channel"]:
        entry["since"] = kept["since"]
    entry["last"] = time.time()
    _hls[(entry["host"], entry["agent"])] = entry


def now():
    t = time.time()
    for key in [k for k, e in _hls.items() if e["last"] < t - HLS_GONE]:
        del _hls[key]
    flowing = [e for e in _open.values() if e.get("taken", e["since"]) > t - STALLED]
    return sorted(({k: v for k, v in e.items() if k not in ("last", "taken")} for e in [*flowing, *_hls.values()]),
                  key=lambda e: e["since"])
