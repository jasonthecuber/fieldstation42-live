import asyncio
import logging
import time

from fs42_live import settings
from fs42_live.scheduled_source import ScheduledSource
from fs42_live.web_source import WebPageSource
from fs42_live.ts import PACKET as TS_PACKET, GopCache

_l = logging.getLogger("FS42LIVE")

VIEWER_BACKLOG = 256  # chunks (~16 MB, over a minute of video); a viewer further behind is dropped


class _Channel:
    """One running source and the viewers sharing it."""

    def __init__(self, station):
        self.name = station["network_name"] + (f" ({station['live_crop_to'] or 'letterbox'})" if station.get("_zoomed") else "")
        self.viewers = set()
        self.closed = False
        self.started = time.monotonic()
        self.bytes_out = 0
        self.held_until = 0.0  # unwatched, it keeps running until then (time.monotonic())
        self.warmed_until = 0.0  # ...because a viewer next door may flip to it
        self._idle = None
        self._gop = GopCache()
        source = WebPageSource(station) if station["network_type"] == "web" else ScheduledSource(station)
        self._task = asyncio.create_task(self._run(source))

    def join(self):
        if self._idle:
            self._idle.cancel()
            self._idle = None
        viewer = asyncio.Queue(maxsize=VIEWER_BACKLOG)
        for chunk in self._gop.snapshot():  # start on the latest keyframe, right away
            viewer.put_nowait(chunk)
        self.viewers.add(viewer)
        _l.info(f"{self.name}: viewer joined ({len(self.viewers)} watching)")
        return viewer

    def leave(self, viewer, grace):
        self.viewers.discard(viewer)
        _l.info(f"{self.name}: viewer left ({len(self.viewers)} watching)")
        self.hold(grace)

    def hold(self, seconds):
        """Keep running at least this long, watched or not."""
        self.held_until = max(self.held_until, time.monotonic() + seconds)
        if not self.viewers and not self.closed and self._idle is None:
            self._idle = asyncio.create_task(self._stop_when_idle())

    def stop(self):
        self._task.cancel()

    def status(self):
        uptime = time.monotonic() - self.started
        return {
            "viewers": len(self.viewers),
            "uptime": round(uptime),
            "kbps": round(self.bytes_out * 8 / 1000 / uptime) if uptime > 0 else 0,
            "idle_stop_pending": self._idle is not None,
            "warm": not self.viewers and time.monotonic() < self.warmed_until,
        }

    async def _stop_when_idle(self):
        while (wait := self.held_until - time.monotonic()) > 0:
            await asyncio.sleep(wait)
        self._idle = None
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
                self._gop.feed(data)
                self.bytes_out += len(data)
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
    """Shares one encode per channel among all of its viewers, started on demand. A viewer who picked their own
    zoom (`zoomed`) shares one with the others who picked the same."""

    def __init__(self):
        self._channels = {}

    async def watch(self, station, surfing=False):
        """Yield the channel's MPEG-TS bytes until the viewer goes away. A surfing viewer (the
        web player) lets the channel stop sooner, since that player warms the ones it may need."""
        channel = self._channel(station)
        viewer = channel.join()
        try:
            while (chunk := await viewer.get()) is not None:
                yield chunk
        finally:
            channel.leave(viewer, settings.get("surf_grace" if surfing else "idle_grace"))

    def warm(self, station):
        """Start a channel ahead of a viewer likely to flip to it. It runs for the surf grace
        unless warmed again or watched."""
        channel = self._channel(station)
        channel.warmed_until = time.monotonic() + settings.get("surf_grace")
        channel.hold(settings.get("surf_grace"))

    def _channel(self, station):
        key = (station["channel_number"], station.get("live_crop_to"))
        channel = self._channels.get(key)
        if channel is None or channel.closed:
            channel = self._channels[key] = _Channel(station)
        return channel

    def status(self):
        """Running channels by number (with their viewers in zooms of their own counted in)."""
        out = {}
        for (number, _), c in sorted(self._channels.items(), key=lambda kv: kv[1].started):
            if c.closed:
                continue
            if number in out:
                out[number]["viewers"] += len(c.viewers)
                out[number]["zooms"] = out[number].get("zooms", 0) + 1
            else:
                out[number] = c.status()
        return out

    def restart(self, number):
        """Stop a channel's encodes; viewers reconnect and get fresh ones. False if it wasn't running."""
        running = [c for (n, _), c in self._channels.items() if n == number and not c.closed]
        for channel in running:
            _l.info(f"{channel.name}: restarted from the control panel")
            channel.stop()
        return bool(running)


hub = ChannelHub()
