"""HLS output, for players that can't take a raw MPEG-TS stream (Safari, the web player).

A channel watched over HLS gets one packager: a viewer of the channel's shared stream, piped
into ffmpeg, which cuts it into segments on keyframes without re-encoding. HLS players fetch
files instead of staying connected, so a packager stops once nothing has been fetched for a
while, and then the channel's own idle timer takes over.
"""

import asyncio
import logging
import os
import shutil
import time

from fs42_live.hub import hub

_l = logging.getLogger("FS42LIVE")

ROOT = "/dev/shm/fs42_hls"  # in memory: segments only live a few seconds
SEGMENT = 2  # seconds; keyframes are every second
LIST_SIZE = 6  # segments in the playlist
IDLE_STOP = 30  # seconds without a fetch
FIRST_PLAYLIST_WAIT = 15  # seconds; a channel that isn't running yet has to start first


class _Packager:
    def __init__(self, station):
        self.name = station["network_name"]
        self.dir = os.path.join(ROOT, str(station["channel_number"]))
        self.playlist = os.path.join(self.dir, "index.m3u8")
        self.last_fetch = time.monotonic()
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.task = asyncio.create_task(self._run(station))

    async def _run(self, station):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mpegts", "-i", "pipe:0",
            "-c", "copy", "-f", "hls", "-hls_time", str(SEGMENT), "-hls_list_size", str(LIST_SIZE),
            "-hls_flags", "delete_segments+independent_segments+omit_endlist+temp_file",
            "-hls_segment_filename", os.path.join(self.dir, "%06d.ts"), self.playlist,
            stdin=asyncio.subprocess.PIPE)
        _l.info(f"{self.name}: HLS started")
        stream = hub.watch(station)
        try:
            async for chunk in stream:
                if time.monotonic() - self.last_fetch > IDLE_STOP:
                    break
                proc.stdin.write(chunk)
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            _l.warning(f"{self.name}: HLS packager quit")
        finally:
            await stream.aclose()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            shutil.rmtree(self.dir, ignore_errors=True)
            _l.info(f"{self.name}: HLS stopped")


_packagers = {}


async def playlist(station):
    """The channel's playlist file, starting its packager if needed. None if it didn't come up."""
    number = station["channel_number"]
    packager = _packagers.get(number)
    if packager is None or packager.task.done():
        packager = _packagers[number] = _Packager(station)
    packager.last_fetch = time.monotonic()
    deadline = time.monotonic() + FIRST_PLAYLIST_WAIT
    while not os.path.exists(packager.playlist):
        if packager.task.done() or time.monotonic() > deadline:
            return None
        await asyncio.sleep(0.25)
    return packager.playlist


def segment(number, name):
    packager = _packagers.get(number)
    if packager is None or packager.task.done():
        return None
    packager.last_fetch = time.monotonic()
    path = os.path.join(packager.dir, name)
    return path if os.path.exists(path) else None
