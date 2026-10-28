"""Live-streaming settings: the `live` section of the main config (confs/main_config.json), e.g.

    "live": {"idle_grace": 30, "surf_grace": 5, "warm_neighbours": 1, "page_load": 0.5}

Anything left out keeps its default. Changes take effect when the server restarts.
"""

from fs42.station_manager import StationManager

DEFAULTS = {
    # seconds a channel keeps running after its last viewer leaves (Jellyfin re-probes, quick flips back)
    "idle_grace": 30,
    # the same, after the web player flips away from it or stops warming it
    "surf_grace": 5,
    # channels started ahead on each side of the one playing in the web player; 0 turns warming off
    "warm_neighbours": 1,
    # seconds a web channel's page gets to draw before capture starts (a station's `live_page_load` overrides it)
    "page_load": 0.5,
    # the loudness every file is turned up or down to (LUFS); US TV aims at -24
    "loudness_target": -24,
    # where Jellyfin is, to tell it when the guide changes (from inside the container: the host's port)
    "jellyfin_url": "http://host.docker.internal:8096",
    # smudge out channel logos burned into shows (BUZZR's, MeTV's), found by logos.py
    "hide_logos": True,
    # a wide movie cropped narrower follows the action instead of keeping the middle, found by panscan.py
    "pan_scan": True,
}


def get(key):
    return StationManager().server_conf.get("live", {}).get(key, DEFAULTS[key])
