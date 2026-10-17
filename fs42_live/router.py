import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse

from fs42.liquid_manager import LiquidManager
from fs42.station_manager import StationManager
from fs42_live import guide, hls, hwaccel, ondemand, reports, settings
from fs42_live.hub import hub
from fs42_live.profile import RETRO

router = APIRouter(prefix="/live", tags=["live"])
_l = logging.getLogger("FS42LIVE")
STATIC = os.path.join(os.path.dirname(__file__), "static")


def _station(channel):
    station = StationManager().station_by_channel(channel)
    if station is None:
        raise HTTPException(status_code=404, detail=f"No station on channel {channel}")
    if station["network_type"] in guide.NOT_YET_STREAMABLE:
        raise HTTPException(status_code=501, detail=f"{station['network_type']} stations can't be streamed yet")
    return station


@router.get("/stream/{channel}.ts")
async def stream_channel(channel: int, surf: bool = False):
    station = _station(channel)
    return StreamingResponse(hub.watch(station, surfing=surf), media_type="video/mp2t")


@router.post("/warm")
async def warm(channels: str):
    """The web player names the channels next to the one it's playing, every few seconds."""
    for n in channels.split(","):
        station = StationManager().station_by_channel(int(n)) if n.isdigit() else None
        if station and station["network_type"] not in guide.NOT_YET_STREAMABLE:
            hub.warm(station)
    return {}


@router.get("/coming_soon.html")
async def coming_soon():
    return FileResponse(os.path.join(STATIC, "coming_soon.html"))


@router.get("/preview_guide.html")
async def preview_guide():
    return FileResponse(os.path.join(STATIC, "preview_guide.html"))


@router.get("/keys.html")
async def keys_page():
    return FileResponse(os.path.join(STATIC, "keys.html"), headers={"Cache-Control": "no-cache"})


@router.post("/keys/log")
async def keys_log(request: Request):
    """The remote test page reports each button press here, so it shows in the server log."""
    _l.info(f"remote test: {(await request.body()).decode(errors='replace')[:200]}")
    return {}


@router.get("/channels.m3u")
async def channels_m3u(request: Request):
    return PlainTextResponse(guide.m3u(str(request.base_url)), media_type="audio/x-mpegurl")


@router.get("/xmltv.xml")
async def xmltv():
    return Response(guide.xmltv(), media_type="application/xml")


@router.get("/")
async def live_page():
    return FileResponse(os.path.join(STATIC, "live.html"), headers={"Cache-Control": "no-cache"})


@router.get("/status")
async def status():
    running = hub.status()
    hw = await asyncio.to_thread(hwaccel.vaapi)
    p = RETRO
    channels = []
    for s in guide.streamable_stations():
        n = s["channel_number"]
        entry = {"channel": n, "name": s["network_name"], "display": guide.display_name(s), "type": s["network_type"],
                 "running": n in running, **running.get(n, {})}
        if s["network_type"] != "web":
            try:
                entry["schedule_ends"] = LiquidManager().get_extents(s["network_name"])[1]
            except ValueError:
                entry["schedule_ends"] = None
        channels.append(entry)
    return {
        "channels": channels,
        "warm_neighbours": settings.get("warm_neighbours"),
        "encoder": f"Quick Sync (h264_vaapi on {hwaccel.DEVICE})" if hw else "CPU (libx264)",
        "profile": f"{p.width}x{p.height} @ {p.fps} fps, video {p.video_bitrate} (max {p.video_maxrate}), "
                   f"audio AAC {p.audio_bitrate}",
    }


@router.post("/channels/{channel}/restart")
async def restart_channel(channel: int):
    return {"restarted": hub.restart(channel)}


@router.get("/hls/{channel}/index.m3u8")
async def hls_playlist(channel: int):
    path = await hls.playlist(_station(channel))
    if path is None:
        raise HTTPException(status_code=503, detail=f"Channel {channel} didn't start")
    return FileResponse(path, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})


@router.get("/hls/{channel}/{name}.ts")
async def hls_segment(channel: int, name: str):
    path = hls.segment(channel, f"{name}.ts") if name.isdigit() else None
    if path is None:
        raise HTTPException(status_code=404, detail="No such segment")
    return FileResponse(path, media_type="video/mp2t")


@router.get("/watch")
async def watch_page():
    return FileResponse(os.path.join(STATIC, "watch.html"), headers={"Cache-Control": "no-cache"})


@router.get("/watch.webmanifest")
async def watch_manifest():
    return FileResponse(os.path.join(STATIC, "watch.webmanifest"), media_type="application/manifest+json")


@router.get("/ondemand/now/{channel}")
async def ondemand_now(channel: int):
    """The show on a channel now, to watch from the start."""
    station = _station(channel)
    show = await asyncio.to_thread(ondemand.now_showing, station)
    if show is None or ondemand.allowed(station, show["file"]) is None:
        raise HTTPException(status_code=404, detail="Nothing to watch from the start on this channel")
    show["duration"] = await ondemand.duration(show["file"])
    return show


@router.get("/ondemand/{channel}.ts")
async def ondemand_stream(channel: int, file: str, start: float = 0):
    station = _station(channel)
    path = ondemand.allowed(station, file)
    length = path and await ondemand.duration(path)
    if not length:
        raise HTTPException(status_code=404, detail="Not in this channel's catalog")
    start = max(0.0, min(start, length - 1))
    _l.info(f"{station['network_name']}: on demand {os.path.basename(path)} from {start:.0f}s")
    return StreamingResponse(ondemand.OnDemandSource(station, path, start, length).stream(), media_type="video/mp2t")


@router.post("/report")
async def report_problem(request: Request):
    """The web player's "Report a problem": the channel, a pick (1-5) and/or a note, and how many
    seconds ago the viewer opened the menu."""
    body = await request.json()
    station = _station(int(body.get("channel", 0)))
    pick, note = int(body.get("pick") or 0), str(body.get("note") or "")
    if (pick not in reports.PICKS or pick == 5) and not note.strip():  # "something else" needs a note
        raise HTTPException(status_code=400, detail="Say what's wrong")
    file = body.get("file")
    watching = (file, float(body.get("at") or 0)) if file and ondemand.allowed(station, file) else None
    saved = await asyncio.to_thread(reports.save, station, pick, note, float(body.get("ago") or 0),
                                    request.headers.get("user-agent", ""), watching)
    _l.info(f"problem report #{saved['number']}: ch {saved['channel']} {saved['problem']} {saved['note']!r}")
    return {"saved": True, "number": saved["number"]}


@router.post("/player/log")
async def player_log(request: Request):
    """The web player's errors, stalls and recoveries, so a TV browser's troubles show in the server log."""
    _l.info(f"player {request.client.host}: {(await request.body()).decode(errors='replace')[:300]}")
    return {}


@router.get("/reports")
async def problem_reports():
    return await asyncio.to_thread(reports.recent)
