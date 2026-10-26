"""Updates a station's catalog when files are added to or removed from its content folder.

Upstream rebuilds catalogs only when asked (web console or `station_42.py -r`), and its
rebuild replaces every catalog entry, so their IDs change and the station's schedule (which
refers to entries by ID) has to be deleted too. This scans the folder the same way but
only inserts new entries and deletes missing ones, so the built schedule keeps working:
sequences keep their place and take in new episodes, and new media shows up as the schedule
keeper adds time. Only when files have gone is the schedule rebuilt, since it may use them.
New entries start at their tag's lowest play count, so they join the rotation evenly.
"""

import json
import logging
import os

from fs42.catalog_api import CatalogAPI
from fs42.catalog_entry import CatalogEntry
from fs42.catalog_io import CatalogIO
from fs42.liquid_manager import LiquidManager
from fs42.catalog import ShowCatalog
from fs42.station_manager import StationManager

_l = logging.getLogger("FS42LIVE")

STATE_DIR = "runtime/live_state"


def refresh_changed_catalogs(keep_schedules=()):
    """Returns True if any catalog was rebuilt. The stations named in keep_schedules keep theirs
    even when files have gone: the caller has already taken those out of it."""
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
            emptied = folders_emptied(station["content_dir"], before, now)
            if emptied:
                # most likely the drive or share behind them isn't mounted, not a real removal
                _l.warning(f"catalog watcher: {name}: no files in {', '.join(emptied)}; leaving its catalog alone")
                continue
            added, gone = len(now - before), len(before - now)
            _l.info(f"catalog watcher: {name} has {added} new and {gone} removed files; updating its catalog")
            update(station, keep_schedule=not gone or name in keep_schedules)
            changed = True
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(state, "w") as f:
            json.dump(sorted(now), f)
    return changed


def folders_emptied(content_dir, before, now):
    """Top-level folders (tags) that had files and now have none, though the folder (or the
    link to it) is still there. Deleting the folder itself is a real removal."""
    def tags(entries):
        return {os.path.relpath(e.split("|")[0], content_dir).split(os.sep)[0] for e in entries}
    return sorted(t for t in tags(before) - tags(now) if os.path.lexists(os.path.join(content_dir, t)))


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


def update(station, keep_schedule):
    if not keep_schedule and station.get("_has_schedule"):
        # before the catalog changes, while the schedule's entries can still be looked up
        LiquidManager().reset_schedule(station)
    ShowCatalog.clear_fluid_cache()
    MergingCatalog(station, rebuild_catalog=True)


class MergingCatalog(ShowCatalog):
    """Builds like upstream, but writes only the changes."""

    def _write_catalog(self):
        scanned = {}
        for entries in self.clip_index.values():
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, CatalogEntry):
                        scanned[(entry.tag, entry.path)] = entry
        stored = {(e.tag, e.path): e for e in CatalogAPI.get_entries(self.config)}
        name = self.config["network_name"]
        new = [e for key, e in scanned.items() if key not in stored]
        # the scheduler plays the least-played first, so new files at 0 would crowd out their tag until caught up
        lowest = {}
        for (tag, _), e in stored.items():
            lowest[tag] = min(lowest.get(tag, e.count), e.count)
        for e in new:
            e.count = lowest.get(e.tag, 0)
        CatalogIO().put_catalog_entries(name, new)
        with CatalogIO()._get_connection() as db:
            for key in stored.keys() - scanned.keys():
                db.execute("DELETE FROM catalog_entries WHERE id = ?", (stored[key].dbid,))
            for key in stored.keys() & scanned.keys():
                # a file replaced under the same name
                if abs(stored[key].duration - scanned[key].duration) > 1:
                    db.execute("UPDATE catalog_entries SET duration = ? WHERE id = ?",
                               (scanned[key].duration, stored[key].dbid))
