"""Puts seasonal ads and specials on the air for a stretch of dates, then takes them off again.

Runs on the host, once a day from cron (see SETUP.md), against the data folder:

    python3 ~/fieldstation42-live/fs42_live/tools/seasons.py [~/fs42-data] [--dry] [--date 2026-10-31]

Seasons are described in <data>/seasons.json:

    {
      "media_root": "/mnt/storage/data/channels",
      "timezone": "America/Chicago",
      "seasons": {
        "halloween": {
          "from": "10-19", "until": "11-01",
          "links": [
            {"from": "commercials/halloween", "to": "primetime/commercial/halloween"},
            {"from": "specials/halloween", "to": "cartoons/halloween", "except": "Some Title"}
          ],
          "slots": {"cartoons": {"16": "halloween", "19": "halloween"}}
        },
        "halloween night": {"from": "10-31", "until": "10-31", "slots": {"cartoons": {"17": "halloween"}}}
      }
    }

Dates are "MM-DD" (every year) or "YYYY-MM-DD", and both ends are included. A link puts each file of a
media folder ("only"/"except" filter on file names) into a folder under the catalog: a commercial folder for
ads, or a folder of its own, which becomes a tag that slots can schedule. Slots override hours of a channel's
day templates (keyed by conf file name), on every day or on "days": ["saturday", ...]; later seasons win.

When the set of seasons in force changes, it rewrites the links and day templates, rebuilds the channels
they touch, adds a week to the schedules and restarts fs42-live. Otherwise it does nothing.
"""

import datetime
import json
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
CONTAINER = "fs42-live"


def in_season(season, today):
    def day(s):
        return datetime.date.fromisoformat(s) if len(s) > 5 else today.replace(month=int(s[:2]), day=int(s[3:]))

    start, end = day(season["from"]), day(season["until"])
    if start <= end:
        return start <= today <= end
    return today >= start or today <= end  # e.g. 12-26 to 01-01


def wanted_links(data, root, seasons):
    links = {}
    for season in seasons:
        for link in season.get("links", []):
            src = f"{root}/{link['from']}"
            for f in sorted(os.listdir(src)):
                if "only" in link and link["only"] not in f or "except" in link and link["except"] in f:
                    continue
                links[f"{data}/catalog/{link['to']}/{f}"] = f"{src}/{f}"
    return links


def sync_links(state, links, dry):
    """Makes exactly the wanted links, removing the ones made for seasons now over; returns the folders touched."""
    touched = set()
    for name in state.get("links", []):
        if name not in links and os.path.islink(name):
            touched.add(os.path.dirname(name))
            if not dry:
                os.remove(name)
                if not os.listdir(os.path.dirname(name)):
                    os.rmdir(os.path.dirname(name))
    for name, target in links.items():
        if not (os.path.islink(name) and os.readlink(name) == target):
            touched.add(os.path.dirname(name))
            if not dry:
                os.makedirs(os.path.dirname(name), exist_ok=True)
                if os.path.islink(name):
                    os.remove(name)
                os.symlink(target, name)
    state["links"] = sorted(links)
    return touched


def sync_templates(data, state, seasons, dry):
    """Points each weekday of a channel at its usual template, or at a copy with the season's hours swapped in."""
    changed = set()
    channels = set(state.get("base", {})) | {c for s in seasons for c in s.get("slots", {})}
    for channel in sorted(channels):
        path = f"{data}/confs/{channel}.json"
        conf = json.load(open(path))
        station = conf["station_conf"]
        templates = station["day_templates"]
        base = state.setdefault("base", {}).setdefault(channel, {d: station[d] for d in DAYS})
        for d in DAYS:
            hours = {}
            for s in seasons:
                slots = s.get("slots", {}).get(channel, {})
                if d in slots.get("days", DAYS):
                    hours.update({h: {"tags": tag} for h, tag in slots.items() if h != "days"})
            if hours:
                name = f"season_{d}"
                templates[name] = {**templates[base[d]], **hours}
                station[d] = name
            else:
                templates.pop(f"season_{d}", None)
                station[d] = base[d]
        if json.load(open(path)) != conf:
            changed.add(channel)
            if not dry:
                json.dump(conf, open(path, "w"), indent=2)
        if all(station[d] == base[d] for d in DAYS):
            del state["base"][channel]
    return changed


def affected(data, folders, channels):
    """Network names of the channels whose content, ads or bumpers include one of the folders."""
    names = set()
    folders = {os.path.realpath(f) for f in folders}
    for conf_file in sorted(os.listdir(f"{data}/confs")):
        if not conf_file.endswith(".json") or not os.path.isfile(f"{data}/confs/{conf_file}"):
            continue  # confs/off: Control Room's removed channels
        station = json.load(open(f"{data}/confs/{conf_file}")).get("station_conf", {})
        if "content_dir" not in station:
            continue
        content = f"{data}/{station['content_dir']}"
        roots = {os.path.realpath(content)} | {os.path.realpath(f"{content}/{station[k]}")
                                               for k in ("commercial_dir", "bump_dir") if station.get(k)}
        if conf_file[:-5] in channels or any(f == r or f.startswith(r + "/") for f in folders for r in roots):
            names.add(station["network_name"])
    return names


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    data = os.path.expanduser(args[0] if args and not args[0][0].isdigit() else "~/fs42-data")
    dry = "--dry" in sys.argv
    config = json.load(open(f"{data}/seasons.json"))
    today = datetime.datetime.now(ZoneInfo(config.get("timezone", "UTC"))).date()
    if "--date" in sys.argv:
        today = datetime.date.fromisoformat(sys.argv[sys.argv.index("--date") + 1])

    state_file = f"{data}/seasons.state.json"
    state = json.load(open(state_file)) if os.path.exists(state_file) else {}
    names = [n for n, s in config["seasons"].items() if in_season(s, today)]
    seasons = [config["seasons"][n] for n in names]

    folders = sync_links(state, wanted_links(data, config["media_root"], seasons), dry)
    channels = sync_templates(data, state, seasons, dry)
    rebuild = affected(data, folders, channels) | set(state.get("pending", []))
    print(f"{today}: {', '.join(names) or 'no season'}; rebuilding {', '.join(sorted(rebuild)) or 'nothing'}")
    state["active"] = names
    if dry:
        return
    state["pending"] = sorted(rebuild)  # so a failed rebuild is retried tomorrow
    json.dump(state, open(state_file, "w"), indent=1)
    if rebuild:
        for name in sorted(rebuild):
            subprocess.run(["docker", "exec", CONTAINER, "python3", "station_42.py", "-r", name], check=True)
        subprocess.run(["docker", "exec", CONTAINER, "python3", "station_42.py", "-w"], check=True)
        subprocess.run(["docker", "restart", CONTAINER], check=True)
        state["pending"] = []
        json.dump(state, open(state_file, "w"), indent=1)


if __name__ == "__main__":
    main()
