from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from fs42.station_manager import StationManager
from fs42_live.scheduled_source import ScheduledSource

router = APIRouter(prefix="/live", tags=["live"])

# Station types that need a source other than the schedule (web pages, external streams)
NOT_YET_STREAMABLE = {"guide", "web", "streaming"}


@router.get("/stream/{channel}.ts")
async def stream_channel(channel: int):
    station = StationManager().station_by_channel(channel)
    if station is None:
        raise HTTPException(status_code=404, detail=f"No station on channel {channel}")
    if station["network_type"] in NOT_YET_STREAMABLE:
        raise HTTPException(status_code=501, detail=f"{station['network_type']} stations can't be streamed yet")
    return StreamingResponse(ScheduledSource(station).stream(), media_type="video/mp2t")
