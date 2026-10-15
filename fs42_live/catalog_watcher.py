"""Rebuilds a station's catalog when files are added to or removed from its content folder.

Upstream rebuilds catalogs only when asked (web console or `station_42.py -r`), and its
rebuild also deletes the station's schedule. When files were only added there's no need
for that: the built schedule still plays, sequences keep their place and take in the new
episodes, and new media shows up as the schedule keeper adds time. Only when files have
gone is the schedule rebuilt as well, since it may point at them.
"""

import json
import logging
import os

from fs42.catalog_api import CatalogAPI
from fs42.liquid_manager import LiquidManager
from fs42.catalog import ShowCatalog
from fs42.station_manager import StationManager

_l = logging.getLogger("FS42LIVE")

STATE_DIR = "runtime/live_state"


def refresh_changed_catalogs():
    """Returns True if any catalog was rebuilt."""
    changed = False
    for station in StationManager().stations:
        if not station.get("_has_catalog") or not station.get("content_dir"):
            continue
        name = station["network_name"]
        now = media_in(station["content_dir"])
        state = os.path.join(STATE_DIR, f"media_{name}.json")
        try:
            with open(state) as f:
                before = set(json.load(f))
        except (OSError, ValueError):
            before = None  # first look: take the catalog as current
        if before is not None and now != before:
            added, gone = len(now - before), len(before - now)
            _l.info(f"catalog watcher: {name} has {added} new and {gone} removed files; rebuilding its catalog")
            rebuild(station, keep_schedule=not gone)
            changed = True
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(state, "w") as f:
            json.dump(sorted(now), f)
    return changed


def media_in(content_dir):
    found = set()
    for folder, dirs, files in os.walk(content_dir, followlinks=True):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file in files:
            if not file.startswith("."):
                path = os.path.join(folder, file)
                try:
                    found.add(f"{path}|{os.path.getsize(path)}")
                except OSError:
                    pass
    return found


def rebuild(station, keep_schedule):
    # the same steps as the web console's catalog rebuild, which always resets the schedule
    if not keep_schedule and station.get("_has_schedule"):
        LiquidManager().reset_schedule(station)
    ShowCatalog.clear_fluid_cache()
    CatalogAPI.delete_catalog(station)
    ShowCatalog(station, rebuild_catalog=True)
