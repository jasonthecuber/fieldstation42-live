"""Who's watching what, for the box's Control Room: a stream counts while it's open, an HLS
player (Safari) while it keeps fetching segments. Jellyfin's Live TV shows up here too, as
streams from Jellyfin's address; Control Room lists those from Jellyfin's side instead."""

import itertools
import time

HLS_GONE = 20  # seconds without a segment fetch before an HLS player counts as gone

_open = {}  # key -> entry
_hls = {}   # (host, agent) -> entry, with "last"
_keys = itertools.count()


def _entry(request, what):
    return {"host": request.client.host if request.client else "", "agent": request.headers.get("user-agent", ""),
            "since": time.time(), **what}


async def counted(request, what, stream):
    """`stream`, with its viewer listed for as long as it's open."""
    key = next(_keys)
    _open[key] = _entry(request, what)
    try:
        async for chunk in stream:
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
    cutoff = time.time() - HLS_GONE
    for key in [k for k, e in _hls.items() if e["last"] < cutoff]:
        del _hls[key]
    return sorted([*_open.values(), *({k: v for k, v in e.items() if k != "last"} for e in _hls.values())],
                  key=lambda e: e["since"])
