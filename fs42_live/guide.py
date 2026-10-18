"""M3U playlist and XMLTV guide, so IPTV clients (Jellyfin) can list and tune the channels."""

import copy
import datetime
import re
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from fs42.liquid_api import LiquidAPI
from fs42.liquid_manager import LiquidManager
from fs42.station_manager import StationManager

# Station types with no live source yet (upstream's Tk guide window, external streams)
NOT_YET_STREAMABLE = {"guide", "streaming"}
GUIDE_HISTORY = datetime.timedelta(hours=6)  # how far back the guide starts
WEB_LISTINGS = datetime.timedelta(days=7)  # how far ahead web channels get listings
HOUR = datetime.timedelta(hours=1)
EPISODE = re.compile(r"\bS(\d+)E(\d+)(?:-?E\d+)*\b\s*(.*)", re.IGNORECASE)
# release tags in a torrent's file name ("Diamonds.N.Dust.1080p.BluRay.x265-KONTRAST"), and all after them
RELEASE_TAGS = re.compile(r"(?:^|[ ._-])(?:\d{3,4}p|blu-?ray|br-?rip|bd-?rip|web-?dl|web-?rip|hdtv|dvd-?rip|"
                          r"x26[45]|[hx]\.26[45]|hevc|xvid|proper|repack|remux)\b.*", re.IGNORECASE)


def streamable_stations():
    return [s for s in StationManager().stations if s["network_type"] not in NOT_YET_STREAMABLE]


def channel_id(station):
    return f"{station['channel_number']}.fs42"


def display_name(station):
    # network_name is the key for schedules and catalogs, so it can't just be renamed
    return station.get("live_display_name") or station["network_name"]


def episode(content_title):
    """(season, episode, name) from a title like "Miami Vice S01E23 Lombard", else None."""
    match = EPISODE.search(content_title or "")
    if not match:
        return None
    name = re.sub(r"[._]+", " ", RELEASE_TAGS.sub("", match[3])).strip(" -")
    return int(match[1]), int(match[2]), name


def m3u(base_url):
    lines = ["#EXTM3U"]
    for s in streamable_stations():
        n, name = s["channel_number"], display_name(s)
        lines.append(
            f'#EXTINF:-1 tvg-id="{channel_id(s)}" tvg-chno="{n}" tvg-name="{name}" '
            f'group-title="FieldStation42",{name}'
        )
        lines.append(f"{base_url}live/stream/{n}.ts")
    return "\n".join(lines) + "\n"


def xmltv():
    tv = ET.Element("tv", {"generator-info-name": "fieldstation42-live"})
    stations = streamable_stations()
    for s in stations:
        ch = ET.SubElement(tv, "channel", id=channel_id(s))
        ET.SubElement(ch, "display-name").text = display_name(s)
        ET.SubElement(ch, "display-name").text = str(s["channel_number"])

    since = datetime.datetime.now() - GUIDE_HISTORY
    for s in stations:
        if s["network_type"] == "web":
            _web_listings(tv, s, since)
            continue
        for block in LiquidManager().get_schedule_by_name(s["network_name"]) or []:
            if block.end_time > since:
                _programme(tv, channel_id(s), block, s)

    ET.indent(tv)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode") + "\n"


def _programme(tv, channel, block, station):
    prog = ET.SubElement(tv, "programme", start=_xmltv_time(block.start_time),
                         stop=_xmltv_time(block.end_time), channel=channel)
    ET.SubElement(prog, "title").text = block_title(station, block) or "FieldStation42"

    content_title = getattr(block.content, "title", None)
    ep = episode(content_title)
    if ep:
        season, number, name = ep
        if name:
            ET.SubElement(prog, "sub-title").text = name
        ET.SubElement(prog, "episode-num", system="onscreen").text = f"S{season:02}E{number:02}"
        ET.SubElement(prog, "episode-num", system="xmltv_ns").text = f"{season - 1}.{number - 1}."
    elif content_title and content_title != block.title:
        ET.SubElement(prog, "sub-title").text = content_title
    ET.SubElement(prog, "previously-shown")


def block_title(station, block):
    # a clip show (music videos) is titled by its folder tag; `live_titles` names it for the guide
    return station.get("live_titles", {}).get(block.title, block.title)


def listing(station):
    return station.get("live_listing") or station["network_name"]


def web_blocks(title, start, end):
    # a web channel shows the same thing all day; list it hourly so guides draw a normal grid
    hour = start.replace(minute=0, second=0, microsecond=0)
    while hour < end:
        yield SimpleNamespace(title=title, start_time=hour, end_time=hour + HOUR, content=None)
        hour += HOUR


def fill_in_retro_guide():
    """Upstream's retro guide only lists scheduled stations, and shows episode titles only from
    NFO metadata. Add listings for the web channels, and episode titles from file names."""
    upstream = LiquidAPI.get_all_blocks

    def get_all_blocks(start, end):
        by_station = upstream(start, end)
        for blocks in by_station.values():
            for block in blocks:
                ep = episode(getattr(block.content, "title", None))
                if ep and ep[2]:
                    # upstream puts NFO metadata over this when there is some
                    block.meta = {"type": "episode", "show_title": block.title, "title": ep[2]}
        for s in StationManager().stations:
            if s.get("live_titles") and s["network_name"] in by_station:
                # copies: the scheduler reads a clip block's title as its tag
                by_station[s["network_name"]] = [_retitled(b, block_title(s, b)) for b in by_station[s["network_name"]]]
            if s["network_type"] == "web" and not s.get("hidden"):
                title = s.get("live_guide_listing") or listing(s)  # the guide shows the channel name beside it
                by_station[s["network_name"]] = list(web_blocks(title, start, end))
        return by_station

    LiquidAPI.get_all_blocks = staticmethod(get_all_blocks)


def _retitled(block, title):
    if title == block.title:
        return block
    block = copy.copy(block)
    block.title = title
    return block


def _web_listings(tv, station, since):
    for block in web_blocks(listing(station), since, since + GUIDE_HISTORY + WEB_LISTINGS):
        prog = ET.SubElement(tv, "programme", start=_xmltv_time(block.start_time),
                             stop=_xmltv_time(block.end_time), channel=channel_id(station))
        ET.SubElement(prog, "title").text = block.title


def _xmltv_time(when):
    # schedules are naive local time (the container's TZ)
    return when.astimezone().strftime("%Y%m%d%H%M%S %z")
