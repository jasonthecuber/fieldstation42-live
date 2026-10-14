import asyncio
import datetime
import logging
import os
import time
from asyncio.subprocess import DEVNULL, PIPE
from dataclasses import dataclass

from fs42.liquid_manager import LiquidManager
from fs42_live.profile import RETRO

_l = logging.getLogger("FS42LIVE")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
MIN_SEGMENT = 1.0  # shorter leftovers are waited out rather than spinning up an encoder
SLATE_SECONDS = 10.0  # how long the standby slate runs before asking the schedule again
CHUNK = 64 * 1024


@dataclass
class Segment:
    path: str | None  # None = black slate
    start: float
    duration: float


class ScheduledSource:
    """One live MPEG-TS stream for a scheduled (standard) station.

    For each segment it asks the schedule what is on now and runs one real-time ffmpeg
    encode of the rest of it. Re-asking at every boundary keeps the stream on the wall
    clock, so ffmpeg start-up time never accumulates as drift. The segments are stamped
    with wall-clock PTS offsets and fed through a long-lived copy remux, so the viewer
    sees a single stream with continuous timestamps and continuity counters.
    """

    _audio_cache = {}

    def __init__(self, station_conf, profile=RETRO):
        self.station = station_conf
        self.network = station_conf["network_name"]
        self.profile = profile
        self._procs = set()

    async def stream(self):
        """Yield MPEG-TS bytes for as long as the caller keeps reading."""
        remux = await self._spawn(
            "remux",
            "-probesize", "200000", "-analyzeduration", "1000000",
            "-f", "mpegts", "-i", "pipe:0",
            "-map", "0", "-c", "copy", "-f", "mpegts", "pipe:1",
            stdin=PIPE,
        )
        feeder = asyncio.create_task(self._feed(remux.stdin))
        _l.info(f"{self.network}: stream started")
        try:
            while chunk := await remux.stdout.read(CHUNK):
                yield chunk
        finally:
            feeder.cancel()
            for proc in self._procs:
                if proc.returncode is None:
                    proc.kill()
            _l.info(f"{self.network}: stream stopped")

    async def _feed(self, sink):
        start = time.monotonic()
        try:
            while True:
                seg = await asyncio.to_thread(self._next_segment)
                if seg.duration < MIN_SEGMENT:
                    await asyncio.sleep(seg.duration)
                    continue
                began = time.monotonic()
                if not await self._encode(seg, began - start, sink):
                    # a broken file must not turn into a tight retry loop on the same play point
                    left = seg.duration - (time.monotonic() - began)
                    if left >= MIN_SEGMENT:
                        slate = self._slate(min(left, SLATE_SECONDS))
                        await self._encode(slate, time.monotonic() - start, sink)
        except asyncio.CancelledError:
            raise
        except Exception:
            _l.exception(f"{self.network}: stream feeder failed")
        finally:
            sink.close()  # ends the remux, which ends the response

    def _next_segment(self):
        try:
            pp = LiquidManager().get_play_point(self.network, datetime.datetime.now())
        except Exception as e:  # no schedule built, or it has run out
            _l.warning(f"{self.network}: no play point ({e}); showing standby")
            pp = None
        if pp is None:
            return self._slate(SLATE_SECONDS)
        entry = pp.plan[pp.index]
        if entry.is_stream:
            _l.warning(f"{self.network}: stream entries are not supported yet; showing standby")
            return self._slate(SLATE_SECONDS)
        return Segment(entry.path, entry.skip + pp.offset, entry.duration - pp.offset)

    def _slate(self, seconds):
        image = self.station.get("standby_image", "runtime/standby.png")
        return Segment(image if os.path.exists(image) else None, 0, seconds)

    async def _encode(self, seg, pts_offset, sink):
        """Encode one segment into the sink. Returns False if ffmpeg failed."""
        p = self.profile
        if seg.path is None:
            video_in = ["-f", "lavfi", "-i", f"color=c=black:s={p.width}x{p.height}:r={p.fps}"]
            has_audio = False
        elif os.path.splitext(seg.path)[1].lower() in IMAGE_EXTS:
            video_in = ["-loop", "1", "-framerate", p.fps, "-i", seg.path]
            has_audio = False
        else:
            video_in = ["-ss", f"{seg.start:.3f}", "-i", seg.path]
            has_audio = await self._has_audio(seg.path)

        _l.info(f"{self.network}: {seg.path} from {seg.start:.1f}s for {seg.duration:.1f}s")
        proc = await self._spawn(
            os.path.basename(seg.path or "slate"),
            "-re", *video_in,
            "-f", "lavfi", "-i", f"anullsrc=r={p.audio_rate}:cl=stereo",
            "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a",
            "-t", f"{seg.duration:.3f}", "-shortest",
            "-vf", p.video_filter(), "-af", "aresample=async=1",
            *p.encode_args(),
            "-f", "mpegts", "-output_ts_offset", f"{pts_offset:.3f}", "pipe:1",
        )
        while chunk := await proc.stdout.read(CHUNK):
            sink.write(chunk)
            await sink.drain()
        return await proc.wait() == 0

    async def _has_audio(self, path):
        if path not in self._audio_cache:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=index", "-of", "csv=p=0", path,
                stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL,
            )
            out, _ = await proc.communicate()
            self._audio_cache[path] = bool(out.strip())
        return self._audio_cache[path]

    async def _spawn(self, tag, *args, stdin=DEVNULL):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", *args,
            stdin=stdin, stdout=PIPE, stderr=PIPE,
        )
        self._procs.add(proc)
        asyncio.create_task(self._drain_log(proc, tag))
        return proc

    async def _drain_log(self, proc, tag):
        # stderr must be read, or a chatty ffmpeg blocks on a full pipe
        while line := await proc.stderr.readline():
            _l.warning(f"{self.network} ffmpeg {tag}: {line.decode(errors='replace').rstrip()}")
        await proc.wait()
        self._procs.discard(proc)
