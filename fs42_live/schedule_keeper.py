"""Keeps every station's schedule built ahead of the clock.

Upstream's LiveScheduleAgent does this from the local player's main loop, which the
headless server doesn't run. This follows the same config (`schedule_agent` in the main
config) and builds the way the web console's "add time" does: in a thread, then reload.
"""

import asyncio
import datetime
import logging

from fs42.liquid_api import LiquidAPI
from fs42.liquid_manager import LiquidManager
from fs42.liquid_schedule import LiquidSchedule
from fs42.station_manager import StationManager
from fs42_live.catalog_watcher import refresh_changed_catalogs

_l = logging.getLogger("FS42LIVE")

CHECK_EVERY = 3600  # seconds
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


def refresh_and_extend():
    # new media first, so time added below can use it
    refresh_changed_catalogs()
    extend_if_needed()


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
        if end is None or end < threshold:
            _l.info(f"schedule keeper: {name} schedule ends {end}; adding a {conf['amount_to_add']}")
            LiquidSchedule(station).add_amount(conf["amount_to_add"])
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
