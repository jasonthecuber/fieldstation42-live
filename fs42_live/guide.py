"""M3U playlist and XMLTV guide, so IPTV clients (Jellyfin) can list and tune the channels."""

import datetime
import re
from xml.etree import ElementTree as ET

from fs42.liquid_manager import LiquidManager
from fs42.station_manager import StationManager

# Station types that need a source other than the schedule (web pages, external streams)
NOT_YET_STREAMABLE = {"guide", "web", "streaming"}
GUIDE_HISTORY = datetime.timedelta(hours=6)  # how far back the guide starts
EPISODE = re.compile(r"\bS(\d+)E(\d+)(?:-?E\d+)*\b\s*(.*)", re.IGNORECASE)


def streamable_stations():
    return [s for s in StationManager().stations if s["network_type"] not in NOT_YET_STREAMABLE]


def channel_id(station):
    return f"{station['channel_number']}.fs42"


def m3u(base_url):
    lines = ["#EXTM3U"]
    for s in streamable_stations():
        n, name = s["channel_number"], s["network_name"]
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
        ET.SubElement(ch, "display-name").text = s["network_name"]
        ET.SubElement(ch, "display-name").text = str(s["channel_number"])

    since = datetime.datetime.now() - GUIDE_HISTORY
    for s in stations:
        for block in LiquidManager().get_schedule_by_name(s["network_name"]) or []:
            if block.end_time > since:
                _programme(tv, channel_id(s), block)

    ET.indent(tv)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode") + "\n"


def _programme(tv, channel, block):
    prog = ET.SubElement(tv, "programme", start=_xmltv_time(block.start_time),
                         stop=_xmltv_time(block.end_time), channel=channel)
    ET.SubElement(prog, "title").text = block.title or "FieldStation42"

    content_title = getattr(block.content, "title", None)
    match = EPISODE.search(content_title or "")
    if match:
        season, episode, name = int(match[1]), int(match[2]), match[3].strip()
        if name:
            ET.SubElement(prog, "sub-title").text = name
        ET.SubElement(prog, "episode-num", system="onscreen").text = f"S{season:02}E{episode:02}"
        ET.SubElement(prog, "episode-num", system="xmltv_ns").text = f"{season - 1}.{episode - 1}."
    elif content_title and content_title != block.title:
        ET.SubElement(prog, "sub-title").text = content_title
    ET.SubElement(prog, "previously-shown")


def _xmltv_time(when):
    # schedules are naive local time (the container's TZ)
    return when.astimezone().strftime("%Y%m%d%H%M%S %z")
