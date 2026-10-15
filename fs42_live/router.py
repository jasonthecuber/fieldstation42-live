import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse

from fs42.station_manager import StationManager
from fs42_live import guide
from fs42_live.hub import hub

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
