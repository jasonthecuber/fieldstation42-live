"""Keeps every station's schedule built ahead of the clock.

Upstream's LiveScheduleAgent does this from the local player's main loop, which the
headless server doesn't run. This follows the same config (`schedule_agent` in the main
config) and builds the way the web console's "add time" does: in a thread, then reload.
"""

import asyncio
import datetime
import json
import os
import logging
import threading
import time

from fs42.catalog_api import CatalogAPI
from fs42.liquid_api import LiquidAPI
from fs42.liquid_io import LiquidIO
from fs42.liquid_manager import LiquidManager
from fs42.liquid_schedule import LiquidSchedule
from fs42.sequence_api import SequenceAPI
from fs42.station_manager import StationManager
from fs42_live.break_finder import rebuilt, scan_new_episodes
from fs42.catalog import ShowCatalog
from fs42_live.catalog_watcher import STATE_DIR, MergingCatalog, media_in, refresh_changed_catalogs, update
from fs42_live import jellyfin_guide

_l = logging.getLogger("FS42LIVE")

CHECK_EVERY = 3600  # seconds
SOON_AT_MOST_EVERY = 60  # seconds between catch-ups asked for by streams
ON_AIR_FIRST = datetime.timedelta(hours=2)  # a schedule ending sooner than this gets a day before others are filled out
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
        more = False
        try:
            replan, more = await asyncio.to_thread(scan_new_episodes)
            if replan:
                await asyncio.to_thread(rebuild, replan)
        except Exception:
            _l.exception("break finder: scan failed")
        if not more:  # with episodes left, it goes straight on
            await asyncio.sleep(CHECK_EVERY)


def rebuild(stations):
    """For schedules planned before their episodes' breaks were found. A channel is planned again
    from the end of what's on now, as replan does, so what's on the air isn't touched. One that
    can't be (not a standard channel, or nothing on now) is reset: each show rewinds to the
    episode that started today and it's rebuilt from midnight, so today's lineup stays."""
    with _busy:
        manager = StationManager()
        for station in stations:
            name = station["network_name"]
            _l.info(f"break finder: rebuilding {name}'s schedule with the new breaks")
            cut = _cut(station) if station.get("_has_schedule") and station["network_type"] == "standard" else None
            if cut:
                _plan_ahead(manager.station_by_name(name) or station, manager)
            else:
                LiquidManager().reset_schedule(station)
        extend_if_needed()
        reload_without_gap(LiquidManager())
    rebuilt(stations)


def _plan_ahead(station, manager):
    threshold = datetime.datetime.now() + TRIGGER_DELTAS[(
        manager.server_conf.get("schedule_agent") or DEFAULT_CONF)["trigger_add_at"]]
    for _ in range(3):  # from a weekday, the first add only runs to Monday
        LiquidSchedule(station).add_amount("week")
        if LiquidAPI.get_blocks(station)[-1].end_time >= threshold:
            break


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
        if _uncatalogued(station):
            # the watcher can build it from the files before the conf naming them is in (a copy from
            # another box), and then sees nothing new: a show on it with none of its files in the catalog
            update(station, keep_schedule=True)
        if cut:
            _plan_ahead(StationManager().station_by_name(network_name), manager)
            _l.info(f"schedule keeper: {network_name} planned again from {cut}")
        extend_if_needed()
        reload_without_gap(LiquidManager())
    return cut


def stations_changed():
    """For Control Room, which just made a channel, removed one or put one back (a conf added to or
    gone from confs/): Retro TV takes it in without a restart. A new channel's catalog is built and
    its schedule planned; a gone one's schedule is deleted (its catalog and episode places stay, for
    putting it back). Returns {"added", "removed": [network names], "restart": [channel numbers whose
    running encodes are now of something else]}."""
    with _busy:
        manager = StationManager()
        before = {s["network_name"]: s["channel_number"] for s in manager.stations}
        manager._reload_stations()
        now = {s["network_name"]: s for s in manager.stations}
        with LiquidIO()._get_connection() as db:
            planned = {r[0] for r in db.execute("SELECT DISTINCT station FROM liquid_blocks")}
            removed = sorted((set(before) | planned) - set(now))
            for name in removed:
                db.execute("DELETE FROM liquid_blocks WHERE station = ?", (name,))
        added = []
        threshold = datetime.datetime.now() + TRIGGER_DELTAS[(
            manager.server_conf.get("schedule_agent") or DEFAULT_CONF)["trigger_add_at"]]
        for name, station in now.items():
            # a new one's (or one put back, or one made again with a deleted one's name): only the
            # differences are written, so one put back keeps its episodes' places
            if station.get("_has_catalog") and station.get("content_dir") and (
                    name not in before or not CatalogAPI.get_entries(station)):
                _l.info(f"schedule keeper: {name} is new; building its catalog")
                ShowCatalog.clear_fluid_cache()
                MergingCatalog(station, rebuild_catalog=True)
                os.makedirs(STATE_DIR, exist_ok=True)  # what the catalog watcher compares against
                with open(os.path.join(STATE_DIR, f"media_{name}.json"), "w") as f:
                    json.dump(sorted(media_in(station["content_dir"])), f)
            if station.get("_has_schedule") and name not in planned:
                station = StationManager().station_by_name(name)
                for _ in range(3):  # from a weekday, the first add only runs to Monday
                    LiquidSchedule(station).add_amount("week")
                    if LiquidAPI.get_blocks(station)[-1].end_time >= threshold:
                        break
                _l.info(f"schedule keeper: {name} planned")
            if name not in before:
                added.append(name)
        reload_without_gap(LiquidManager())
    numbers = {n for name, n in before.items() if name not in now or now[name]["channel_number"] != n}
    numbers |= {s["channel_number"] for name, s in now.items() if name not in before}
    return {"added": sorted(added), "removed": removed, "restart": sorted(numbers)}


def _uncatalogued(station):
    """Tags its day templates play that its catalog has nothing for, though their folders are there."""
    tags = set()
    for template in (station.get("day_templates") or {}).values():
        for slot in template.values() if isinstance(template, dict) else ():
            t = slot.get("tags") if isinstance(slot, dict) else None
            tags.update(x for x in ([t] if isinstance(t, str) else t or []) if isinstance(x, str))
    if not station.get("_has_catalog") or not station.get("content_dir"):
        return set()
    have = {e.tag for e in CatalogAPI.get_entries(station)}
    return {t for t in tags - have if os.path.isdir(os.path.join(station["content_dir"], t))}


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
    now = datetime.datetime.now()
    threshold = now + TRIGGER_DELTAS[conf["trigger_add_at"]]
    liquid = LiquidManager()
    extended = False
    short = []
    for station in StationManager().stations:
        if not station.get("_has_schedule"):
            continue
        try:
            _, end = liquid.get_extents(station["network_name"])
        except ValueError:
            continue
        if end is None or end < threshold:
            short.append((station, end))
    try:
        # first, a day for each channel with nothing (or hardly anything) left to play, put on the air
        # as soon as it's built: many rebuilt at once take minutes each, and the rest would show standby
        # until the last was done
        for station, end in short:
            if end is not None and end >= now + ON_AIR_FIRST:
                continue
            name = station["network_name"]
            try:
                _l.info(f"schedule keeper: {name} has nothing on soon; planning a day of it first")
                extended = True
                LiquidSchedule(station).add_amount("day")
                liquid.schedules[name] = LiquidAPI.get_blocks(station)
            except Exception:
                _l.exception(f"schedule keeper: can't plan {name}; going on to the rest")
        # then each filled out to the full amount
        for station, _ in short:
            name = station["network_name"]
            try:
                _, end = liquid.get_extents(name)
            except ValueError:
                continue
            try:
                # a fresh schedule only runs to the next Monday, so it can take two adds
                for _ in range(3):
                    if end is not None and end >= threshold:
                        break
                    _l.info(f"schedule keeper: {name} schedule ends {end}; adding a {conf['amount_to_add']}")
                    extended = True
                    LiquidSchedule(station).add_amount(conf["amount_to_add"])
                    end = LiquidAPI.get_blocks(station)[-1].end_time
            except Exception:
                # one channel that can't be planned mustn't leave the ones after it unplanned
                _l.exception(f"schedule keeper: can't plan {name}; going on to the rest")
    finally:
        if extended:  # what was planned plays, even if something after it failed
            reload_without_gap(liquid)
            for station in StationManager().stations:
                if station.get("_has_schedule"):
                    name = station["network_name"]
                    try:
                        _l.info(f"schedule keeper: {name} now runs to {liquid.get_extents(name)[1]}")
                    except ValueError:
                        pass


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
