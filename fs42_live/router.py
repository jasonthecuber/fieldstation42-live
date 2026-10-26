import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse

from fs42.liquid_manager import LiquidManager
from fs42.station_manager import StationManager
from fs42_live import audience, guide, hls, hwaccel, messages, ondemand, reports, schedule_keeper, settings
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


ZOOMS = ("letterbox", "16:9", "14:9", "4:3")


def _zoomed(station, crop):
    """The station as a viewer who picked their own zoom (`crop`) sees it: wide pictures letterboxed, or their
    sides cropped to that shape, instead of the channel's live_crop_to."""
    if not crop or station["network_type"] == "web":
        return station
    if crop not in ZOOMS:
        raise HTTPException(status_code=400, detail=f"crop is one of {', '.join(ZOOMS)}")
    crop_to = None if crop == "letterbox" else crop
    if crop_to == station.get("live_crop_to"):
        return station
    return {**station, "live_crop_to": crop_to, "_zoomed": True}


@router.get("/stream/{channel}.ts")
async def stream_channel(request: Request, channel: int, surf: bool = False, crop: str = ""):
    station = _zoomed(_station(channel), crop)
    what = {"kind": "live", "channel": channel, "name": guide.display_name(station)}
    return StreamingResponse(audience.counted(request, what, hub.watch(station, surfing=surf)),
                             media_type="video/mp2t")


@router.post("/warm")
async def warm(channels: str, crop: str = ""):
    """The web player names the channels next to the one it's playing, every few seconds."""
    for n in channels.split(","):
        station = StationManager().station_by_channel(int(n)) if n.isdigit() else None
        if station and station["network_type"] not in guide.NOT_YET_STREAMABLE:
            hub.warm(_zoomed(station, crop))
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
            entry["crop"] = s.get("live_crop_to")
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


@router.post("/catalogs/refresh")
async def refresh_catalogs():
    """Control Room, after it deletes something on the channels or puts it back: the schedules
    catch up now, not within the hour."""
    schedule_keeper.refresh_now()
    return {}


@router.post("/replan/{network_name}")
async def replan(network_name: str):
    """Control Room, after it puts a show on a channel or takes one off: that channel's schedule is
    planned again from the end of what's on now. Answers when that's done."""
    try:
        start = await asyncio.to_thread(schedule_keeper.replan, network_name)
    except KeyError:
        raise HTTPException(404, f"No channel named {network_name}")
    return {"from": start.isoformat() if start else None}


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
async def hls_segment(request: Request, channel: int, name: str):
    path = hls.segment(channel, f"{name}.ts") if name.isdigit() else None
    if path is None:
        raise HTTPException(status_code=404, detail="No such segment")
    audience.fetched(request, {"kind": "live", "channel": channel, "name": guide.display_name(_station(channel))})
    return FileResponse(path, media_type="video/mp2t")


@router.get("/watch")
async def watch_page():
    return FileResponse(os.path.join(STATIC, "watch.html"), headers={"Cache-Control": "no-cache"})


@router.get("/watch.webmanifest")
async def watch_manifest():
    return FileResponse(os.path.join(STATIC, "watch.webmanifest"), media_type="application/manifest+json")


@router.get("/icon-{size}.png")
async def icon(size: int):
    """The player's icon (static/icon.svg, rendered), for a phone's home screen."""
    if size not in (192, 512):
        raise HTTPException(status_code=404)
    return FileResponse(os.path.join(STATIC, f"icon-{size}.png"), headers={"Cache-Control": "max-age=86400"})


@router.get("/ondemand/now/{channel}")
async def ondemand_now(channel: int):
    """The show on a channel now, to watch from the start."""
    station = _station(channel)
    show = await asyncio.to_thread(ondemand.now_showing, station)
    if show is None or ondemand.allowed(station, show["file"]) is None:
        raise HTTPException(status_code=404, detail="Nothing to watch from the start on this channel")
    show["duration"] = await ondemand.duration(show["file"])
    return show


@router.get("/ondemand/catalog")
async def ondemand_catalogs():
    return await asyncio.to_thread(ondemand.catalogs)


@router.get("/ondemand/catalog/{channel}")
async def ondemand_catalog(channel: int):
    """Everything on a channel to watch on demand, in order (so the next episode is the next one)."""
    station = _station(channel)
    return {"channel": channel, "items": await asyncio.to_thread(ondemand.catalog, station)}


@router.get("/ondemand/retro/{channel}")
async def ondemand_retro(channel: int, file: str, plan: str = ""):
    """Plans a show as it would air, with the channel's commercials and bumpers in its breaks (or, given
    the plan a device was watching it with, that one again if it's still kept). Its stream is
    /live/ondemand/N.ts with plan=<id>, and start is then seconds into the plan."""
    station = _station(channel)
    if ondemand.allowed(station, file) is None:
        raise HTTPException(status_code=404, detail="Not in this channel's catalog")
    kept = plan and await asyncio.to_thread(ondemand.plan, plan)
    if kept and kept["file"] == file:
        return kept
    try:
        plan = await asyncio.to_thread(ondemand.retro, station, file)
    except Exception as e:
        _l.warning(f"{station['network_name']}: couldn't plan breaks for {os.path.basename(file)}: {e}")
        plan = None
    if plan is None:
        raise HTTPException(status_code=404, detail="Couldn't plan breaks for that")
    return plan


@router.get("/ondemand/shuffle/{channel}")
async def ondemand_shuffle(channel: int, breaks: bool = True, plan: str = ""):
    """A music channel's videos shuffled (with an ident and an ad or two every few videos, unless breaks=0),
    or the shuffle given if it's still kept (in its order, with its breaks switched, if that's changed). Its
    stream is /live/ondemand/N.ts with file=shuffle:N and plan=<id>."""
    station = _station(channel)
    kept = plan and await asyncio.to_thread(ondemand.plan, plan)
    order = None
    if kept and kept["file"] == f"shuffle:{channel}":
        if kept.get("breaks", True) == breaks:
            return kept
        order = [i["file"] for i in kept["items"] if i["type"] == "feature"]
    shuffled = await asyncio.to_thread(ondemand.shuffle, station, breaks, order)
    if shuffled is None:
        raise HTTPException(status_code=404, detail="No videos to shuffle on this channel")
    return shuffled


@router.get("/ondemand/{channel}.ts")
async def ondemand_stream(request: Request, channel: int, file: str, start: float = 0, plan: str = "",
                          crop: str = ""):
    station = _zoomed(_station(channel), crop)
    path = ondemand.allowed(station, file)
    if plan:
        p = await asyncio.to_thread(ondemand.plan, plan)
        if p is None or p["file"] != file:
            raise HTTPException(status_code=404, detail="That plan has gone")
        start = max(0.0, min(start, p["duration"] - 1))
        segments = ondemand.plan_segments(p, start)
    else:
        length = path and await ondemand.duration(path)
        if not length:
            raise HTTPException(status_code=404, detail="Not in this channel's catalog")
        start = max(0.0, min(start, length - 1))
        segments = [ondemand.Segment(path, start, length - start)]
    _l.info(f"{station['network_name']}: on demand {os.path.basename(file)} from {start:.0f}s{' (retro)' if plan else ''}")
    what = {"kind": "ondemand", "channel": channel, "name": guide.display_name(station),
            "title": "Shuffle" if file.startswith("shuffle:") else os.path.splitext(os.path.basename(file))[0]}
    return StreamingResponse(audience.counted(request, what, ondemand.OnDemandSource(station, segments).stream()),
                             media_type="video/mp2t")


@router.get("/viewers")
async def viewers():
    """What's being watched now, and from where (the box's Control Room lists them), with what's on
    the live channels."""
    found = audience.now()
    on_now = {}
    for n in {v["channel"] for v in found if v["kind"] == "live"}:
        try:
            show = await asyncio.to_thread(ondemand.now_showing, _station(n))
        except HTTPException:
            show = None
        on_now[n] = show and " · ".join(filter(None, [show["title"], show["episode"]]))
    return [{**v, "on_now": on_now.get(v["channel"])} if v["kind"] == "live" else v for v in found]


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


@router.get("/messages")
async def get_messages():
    return await asyncio.to_thread(messages.current)


@router.post("/messages")
async def put_message(request: Request):
    """{"kind": "crawl", "channel": 12 or "all", "text": "Hi Doug!", "minutes": 30}, or {"kind": "greeting",
    "text": ..., "sub": ..., "minutes": ...} for the greeting channel. "minutes": 0 keeps it on until it's taken
    off; no text takes it off. Channels showing
    a changed crawl restart, so it's on (or off) right away; their viewers reconnect in a moment."""
    body = await request.json()
    kind, text = body.get("kind"), str(body.get("text") or "")
    minutes = float(messages.DEFAULT_MINUTES if body.get("minutes") is None else body["minutes"])
    if kind == "crawl":
        channel = str(body.get("channel") or "all")
        if channel != "all":
            _station(int(channel))
        saved = await asyncio.to_thread(messages.put, f"crawl:{channel}", text, minutes)
        for n in list(hub.status()) if channel == "all" else [int(channel)]:
            hub.restart(n)
    elif kind == "greeting":
        saved = await asyncio.to_thread(messages.put, "greeting", text, minutes, sub=str(body.get("sub") or "")[:200])
    else:
        raise HTTPException(status_code=400, detail='kind is "crawl" or "greeting"')
    _l.info(f"message {kind} {body.get('channel', '')}: {text[:80]!r} " + (f"for {minutes:g} min" if minutes else "until taken off") if text.strip()
            else f"message {kind} {body.get('channel', '')} cleared")
    return saved


@router.get("/greeting.html")
async def greeting_page():
    return FileResponse(os.path.join(STATIC, "greeting.html"), headers={"Cache-Control": "no-cache"})


@router.post("/keylog")
async def key_log(request: Request):
    """The web player's ?keys mode: each key a remote sends, for fitting the player to a new remote."""
    line = (await request.body())[:400].decode(errors="replace")
    _l.info(f"keylog {request.client.host}: {line}")


@router.get("/reports")
async def problem_reports():
    return await asyncio.to_thread(reports.recent)
