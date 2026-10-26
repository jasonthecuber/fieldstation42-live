"""Keeps every station's schedule built ahead of the clock.

Upstream's LiveScheduleAgent does this from the local player's main loop, which the
headless server doesn't run. This follows the same config (`schedule_agent` in the main
config) and builds the way the web console's "add time" does: in a thread, then reload.
"""

import asyncio
import datetime
import os
import logging
import threading
import time

from fs42.liquid_api import LiquidAPI
from fs42.liquid_io import LiquidIO
from fs42.liquid_manager import LiquidManager
from fs42.liquid_schedule import LiquidSchedule
from fs42.sequence_api import SequenceAPI
from fs42.station_manager import StationManager
from fs42_live.break_finder import rebuilt, scan_new_episodes
from fs42_live.catalog_watcher import refresh_changed_catalogs
from fs42_live import jellyfin_guide

_l = logging.getLogger("FS42LIVE")

CHECK_EVERY = 3600  # seconds
SOON_AT_MOST_EVERY = 60  # seconds between catch-ups asked for by streams
# Keep at least a week built, so the guide (XMLTV) always has days of listings
DEFAULT_CONF = {"trigger_add_at": "week", "amount_to_add": "week"}
TRIGGER_DELTAS = {
    "day": datetime.timedelta(days=1),
    "week": datetime.timedelta(weeks=1),
    "month": datetime.timedelta(days=30),
}


async def keep_schedules_built():
    while True:
        try:
            await asyncio.to_thread(refresh_and_extend)
        except Exception:
            _l.exception("schedule keeper: check failed")
        await asyncio.sleep(CHECK_EVERY)


_busy = threading.Lock()
_last_soon = 0.0


def refresh_and_extend(started=None):
    with _busy:
        if started:
            started()
        # new media first, so time added below can use it
        refresh_changed_catalogs()
        extend_if_needed()
        reload_if_changed()


_db_seen = None


def reload_if_changed():
    # schedules rebuilt outside this process (station_42.py on the command line) are only
    # on disk; streams would keep playing the old ones, file names and all
    global _db_seen
    mtime = os.path.getmtime(StationManager().server_conf["db_path"])
    if _db_seen is not None and mtime != _db_seen:
        _l.info("schedule keeper: schedules changed on disk; reloading")
        reload_without_gap(LiquidManager())
    _db_seen = mtime


def refresh_soon():
    """For a stream that found a scheduled file missing: catch up now, not within the hour."""
    global _last_soon
    if _busy.locked() or time.monotonic() - _last_soon < SOON_AT_MOST_EVERY:
        return
    _last_soon = time.monotonic()
    threading.Thread(target=_refresh_quietly, daemon=True).start()


_waiting = threading.Lock()


def refresh_now():
    """For Control Room, which just took things off the channels or put them back: catch up now.
    One already running may have looked before the change, so this waits its turn; one waiting
    is enough for any number of changes."""
    if not _waiting.acquire(blocking=False):
        return
    threading.Thread(target=_refresh_quietly, args=(_waiting.release,), daemon=True).start()


def _refresh_quietly(started=None):
    try:
        refresh_and_extend(started)
    except Exception:
        _l.exception("schedule keeper: catch-up failed")


async def keep_breaks_found():
    while True:
        try:
            replan = await asyncio.to_thread(scan_new_episodes)
            if replan:
                await asyncio.to_thread(rebuild, replan)
        except Exception:
            _l.exception("break finder: scan failed")
        await asyncio.sleep(CHECK_EVERY)


def rebuild(stations):
    """For schedules planned before their episodes' breaks were found. A reset rewinds each
    show to the episode that started today and rebuilds from midnight, so today's lineup stays."""
    with _busy:
        for station in stations:
            _l.info(f"break finder: rebuilding {station['network_name']}'s schedule with the new breaks")
            LiquidManager().reset_schedule(station)
        extend_if_needed()
    rebuilt(stations)


def replan(network_name):
    """For Control Room, which just put a show on a channel or took one off (its conf and catalog
    folder changed): the channel's schedule is planned again from the end of what's on now, so the
    change airs within the hour or so, not after the week already planned. Returns when the new
    plan starts (None if it has no schedule); KeyError if there's no such channel."""
    with _busy:
        manager = StationManager()
        manager._reload_stations()  # its conf may have changed
        station = manager.station_by_name(network_name)
        if station is None:
            raise KeyError(network_name)
        cut = _cut(station) if station.get("_has_schedule") and station["network_type"] == "standard" else None
        refresh_changed_catalogs(keep_schedules=(network_name,))
        if cut:
            threshold = datetime.datetime.now() + TRIGGER_DELTAS[(
                manager.server_conf.get("schedule_agent") or DEFAULT_CONF)["trigger_add_at"]]
            station = StationManager().station_by_name(network_name)
            for _ in range(3):  # from a weekday, the first add only runs to Monday
                LiquidSchedule(station).add_amount("week")
                if LiquidAPI.get_blocks(station)[-1].end_time >= threshold:
                    break
            _l.info(f"schedule keeper: {network_name} planned again from {cut}")
        extend_if_needed()
        reload_without_gap(LiquidManager())
    return cut


def _cut(station):
    """Keeps the schedule up to the end of what's on now (its start, if a file of it is gone, so
    something else plays), winds each show's episodes back to its first one after that, and deletes
    the rest. Returns that time, or None if nothing's planned from now."""
    now = datetime.datetime.now()
    blocks = LiquidAPI.get_blocks(station)
    on = next((b for b in blocks if b.end_time > now), None)
    if on is None:
        return None
    cut = on.end_time
    if on.start_time <= now and any(not p.is_stream and not os.path.exists(p.path) for p in on.plan or []):
        cut = on.start_time
    seen = set()
    for b in blocks:
        if b.start_time >= cut and b.sequence_key and b.content and not isinstance(b.content, list):
            key = (b.sequence_key["sequence_name"], b.sequence_key["tag_path"])
            if key not in seen:
                seen.add(key)
                SequenceAPI.reset_by_episode_path(station, *key, b.content.path)
    with LiquidIO()._get_connection() as db:
        db.execute("DELETE FROM liquid_blocks WHERE station = ? AND start_time >= ?",
                   (station["network_name"], cut))
    return cut


def extend_if_needed():
    conf = StationManager().server_conf.get("schedule_agent") or DEFAULT_CONF
    threshold = datetime.datetime.now() + TRIGGER_DELTAS[conf["trigger_add_at"]]
    liquid = LiquidManager()
    extended = False
    for station in StationManager().stations:
        if not station.get("_has_schedule"):
            continue
        name = station["network_name"]
        try:
            _, end = liquid.get_extents(name)
        except ValueError:
            continue
        # a fresh schedule only runs to the next Monday, so it can take two adds
        for _ in range(3):
            if end is not None and end >= threshold:
                break
            _l.info(f"schedule keeper: {name} schedule ends {end}; adding a {conf['amount_to_add']}")
            LiquidSchedule(station).add_amount(conf["amount_to_add"])
            end = LiquidAPI.get_blocks(station)[-1].end_time
            extended = True
    if extended:
        reload_without_gap(liquid)
        for station in StationManager().stations:
            if station.get("_has_schedule"):
                name = station["network_name"]
                _l.info(f"schedule keeper: {name} now runs to {liquid.get_extents(name)[1]}")


def reload_without_gap(liquid):
    # upstream's reload_schedules() empties the schedules before refilling them, so a
    # live stream asking for its next segment mid-reload would find nothing
    liquid.station_configs = StationManager().stations
    liquid.schedules = {
        station["network_name"]: LiquidAPI.get_blocks(station)
        for station in liquid.station_configs
        if station["network_type"] not in ("guide", "streaming")
    }
    jellyfin_guide.refresh_soon()
