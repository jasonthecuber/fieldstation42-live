import asyncio
import logging

from fs42_live.scheduled_source import ScheduledSource

_l = logging.getLogger("FS42LIVE")

TS_PACKET = 188
IDLE_GRACE = 30.0  # keep an unwatched channel running this long (channel flips, Jellyfin re-probes)
VIEWER_BACKLOG = 256  # chunks (~16 MB, over a minute of video); a viewer further behind is dropped


class _Channel:
    """One running source and the viewers sharing it."""

    def __init__(self, station):
        self.name = station["network_name"]
        self.viewers = set()
        self.closed = False
        self._idle = None
        self._task = asyncio.create_task(self._run(ScheduledSource(station)))

    def join(self):
        if self._idle:
            self._idle.cancel()
            self._idle = None
        viewer = asyncio.Queue(maxsize=VIEWER_BACKLOG)
        self.viewers.add(viewer)
        _l.info(f"{self.name}: viewer joined ({len(self.viewers)} watching)")
        return viewer

    def leave(self, viewer):
        self.viewers.discard(viewer)
        _l.info(f"{self.name}: viewer left ({len(self.viewers)} watching)")
        if not self.viewers and not self.closed and self._idle is None:
            self._idle = asyncio.create_task(self._stop_when_idle())

    async def _stop_when_idle(self):
        await asyncio.sleep(IDLE_GRACE)
        if not self.viewers:
            self._task.cancel()

    async def _run(self, source):
        carry = b""
        try:
            async for chunk in source.stream():
                # whole TS packets only, so a viewer who joins mid-stream starts on a packet
                data = carry + chunk
                cut = len(data) - len(data) % TS_PACKET
                data, carry = data[:cut], data[cut:]
                for viewer in list(self.viewers):
                    try:
                        viewer.put_nowait(data)
                    except asyncio.QueueFull:
                        _l.warning(f"{self.name}: dropping a viewer that fell too far behind")
                        self.viewers.discard(viewer)
                        self._end(viewer)
        finally:
            self.closed = True
            for viewer in self.viewers:
                self._end(viewer)

    @staticmethod
    def _end(viewer):
        if viewer.full():
            viewer.get_nowait()  # make room for the end marker
        viewer.put_nowait(None)


class ChannelHub:
    """Shares one encode per channel among all of its viewers, started on demand."""

    def __init__(self):
        self._channels = {}

    async def watch(self, station):
        """Yield the channel's MPEG-TS bytes until the viewer goes away."""
        number = station["channel_number"]
        channel = self._channels.get(number)
        if channel is None or channel.closed:
            channel = self._channels[number] = _Channel(station)
        viewer = channel.join()
        try:
            while (chunk := await viewer.get()) is not None:
                yield chunk
        finally:
            channel.leave(viewer)


hub = ChannelHub()
