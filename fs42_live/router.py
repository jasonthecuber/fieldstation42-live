import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse

from fs42.liquid_manager import LiquidManager
from fs42.station_manager import StationManager
from fs42_live import guide, hwaccel
from fs42_live.hub import hub
from fs42_live.profile import RETRO

router = APIRouter(prefix="/live", tags=["live"])
STATIC = os.path.join(os.path.dirname(__file__), "static")


@router.get("/stream/{channel}.ts")
async def stream_channel(channel: int):
    station = StationManager().station_by_channel(channel)
    if station is None:
        raise HTTPException(status_code=404, detail=f"No station on channel {channel}")
    if station["network_type"] in guide.NOT_YET_STREAMABLE:
        raise HTTPException(status_code=501, detail=f"{station['network_type']} stations can't be streamed yet")
    return StreamingResponse(hub.watch(station), media_type="video/mp2t")


@router.get("/coming_soon.html")
async def coming_soon():
    return FileResponse(os.path.join(STATIC, "coming_soon.html"))


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
        entry = {"channel": n, "name": s["network_name"], "type": s["network_type"],
                 "running": n in running, **running.get(n, {})}
        if s["network_type"] != "web":
            try:
                entry["schedule_ends"] = LiquidManager().get_extents(s["network_name"])[1]
            except ValueError:
                entry["schedule_ends"] = None
        channels.append(entry)
    return {
        "channels": channels,
        "encoder": f"Quick Sync (h264_vaapi on {hwaccel.DEVICE})" if hw else "CPU (libx264)",
        "profile": f"{p.width}x{p.height} @ {p.fps} fps, video {p.video_bitrate} (max {p.video_maxrate}), "
                   f"audio AAC {p.audio_bitrate}",
    }


@router.post("/channels/{channel}/restart")
async def restart_channel(channel: int):
    return {"restarted": hub.restart(channel)}
