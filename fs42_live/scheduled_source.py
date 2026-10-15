import asyncio
import datetime
import logging
import os
import time
from asyncio.subprocess import DEVNULL, PIPE
from dataclasses import dataclass

from fs42.liquid_manager import LiquidManager
from fs42_live import hwaccel
from fs42_live.profile import RETRO
from fs42_live.schedule_keeper import refresh_soon

_l = logging.getLogger("FS42LIVE")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
LEAD = 1.5  # each encode starts this long before its slot, hiding ffmpeg start-up
MIN_SEGMENT = 0.1  # slivers shorter than this are skipped
MIN_FALLBACK = 1.0  # a failed encode with less than this left isn't worth a slate
SLATE_SECONDS = 10.0  # how long the standby slate runs before asking the schedule again
RESYNC = 5.0  # if planning falls this far behind the wall clock, jump forward
STARTUP = 0.5  # rough ffmpeg start-up time, used to estimate how much a failed encode produced
NUDGE = datetime.timedelta(milliseconds=1)  # at exactly a block's end the schedule has no answer
CHUNK = 64 * 1024


@dataclass
class Segment:
    path: str | None  # None = black slate
    start: float
    duration: float


class ScheduledSource:
    """One live MPEG-TS stream for a scheduled (standard) station.

    Segments are planned back to back on the schedule's own clock: each one starts where
    the last ended, both in schedule time and in stream timestamps, so the output has no
    gaps. Each segment is its own real-time ffmpeg encode, started LEAD seconds before
    its slot so it is already producing when the previous one finishes. The encodes are
    fed through a long-lived copy remux, so the viewer sees a single stream with
    continuous continuity counters.
    """

    _audio_cache = {}

    def __init__(self, station_conf, profile=RETRO):
        self.station = station_conf
        self.network = station_conf["network_name"]
        self.profile = profile
        self._procs = set()
        self._tasks = set()

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
            for task in self._tasks:
                task.cancel()
            for proc in self._procs:
                if proc.returncode is None:
                    proc.kill()
            _l.info(f"{self.network}: stream stopped")

    async def _feed(self, sink):
        """Write the planned encodes into the sink, one after another."""
        encodes = asyncio.Queue(maxsize=1)  # holds the next encode while the current one plays
        self._start(self._plan(encodes))
        try:
            while (out := await encodes.get()) is not None:
                while (chunk := await out.get()) is not None:
                    sink.write(chunk)
                    await sink.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            _l.exception(f"{self.network}: stream writer failed")
        finally:
            sink.close()  # ends the remux, which ends the response

    async def _plan(self, encodes):
        when = datetime.datetime.now()  # schedule time the next segment starts at
        pts = 0.0  # stream time it starts at
        try:
            while True:
                behind = (datetime.datetime.now() - when).total_seconds()
                if behind > RESYNC:
                    _l.warning(f"{self.network}: {behind:.1f}s behind schedule; skipping ahead")
                    when = datetime.datetime.now()
                seg = await asyncio.to_thread(self._segment_at, when)
                if seg.duration >= MIN_SEGMENT:
                    wait = (when - datetime.datetime.now()).total_seconds() - LEAD
                    if wait > 0:
                        await asyncio.sleep(wait)
                    out = asyncio.Queue()  # holds at most about LEAD seconds of output
                    self._start(self._encode(seg, pts, out))
                    await encodes.put(out)
                when += datetime.timedelta(seconds=seg.duration)
                pts += seg.duration
        except asyncio.CancelledError:
            raise
        except Exception:
            _l.exception(f"{self.network}: stream planner failed")
            await encodes.put(None)

    def _segment_at(self, when):
        for attempt in range(3):
            try:
                pp = LiquidManager().get_play_point(self.network, when + NUDGE)
                break
            except Exception as e:  # no schedule built, it has run out, or it is mid-reload
                if attempt < 2:
                    time.sleep(0.2)  # a reload empties the schedules briefly; this runs in a thread
                    continue
                _l.warning(f"{self.network}: no play point ({e}); showing standby")
                pp = None
        if pp is None:
            return self._slate(SLATE_SECONDS)
        entry = pp.plan[pp.index]
        if entry.is_stream:
            _l.warning(f"{self.network}: stream entries are not supported yet; showing standby")
            return self._slate(SLATE_SECONDS)
        if not os.path.exists(entry.path):
            # deleted (or its drive is gone); the catalog watcher catches up, then the
            # schedule moves on. Until then, short slates so it switches over quickly
            _l.warning(f"{self.network}: {entry.path} is missing; showing standby")
            refresh_soon()
            return self._slate(min(SLATE_SECONDS, entry.duration - pp.offset))
        offset = max(0.0, pp.offset - NUDGE.total_seconds())
        return Segment(entry.path, entry.skip + offset, entry.duration - offset)

    def _slate(self, seconds):
        image = self.station.get("standby_image", "runtime/standby.png")
        return Segment(image if os.path.exists(image) else None, 0, seconds)

    async def _encode(self, seg, pts, out):
        """Encode one segment into out, falling back to the slate if ffmpeg fails."""
        try:
            began = time.monotonic()
            if await self._run_encode(seg, pts, out):
                return
            # a broken file must not leave a hole, or the stream stalls until the next slot
            done = max(0.0, time.monotonic() - began - STARTUP)
            left = seg.duration - done
            if left >= MIN_FALLBACK:
                await self._run_encode(self._slate(min(left, seg.duration)), pts + done, out)
        except asyncio.CancelledError:
            raise
        except Exception:
            _l.exception(f"{self.network}: encode of {seg.path} failed")
        finally:
            out.put_nowait(None)

    async def _run_encode(self, seg, pts, out):
        """Returns False if ffmpeg failed."""
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

        hw = await asyncio.to_thread(hwaccel.vaapi)
        _l.info(f"{self.network}: {seg.path} from {seg.start:.1f}s for {seg.duration:.1f}s")
        proc = await self._spawn(
            os.path.basename(seg.path or "slate"),
            *hwaccel.input_args(), "-re", *video_in,
            "-f", "lavfi", "-i", f"anullsrc=r={p.audio_rate}:cl=stereo",
            "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a",
            "-t", f"{seg.duration:.3f}", "-shortest",
            "-vf", p.video_filter(hw), "-af", "aresample=async=1",
            *p.encode_args(hw),
            "-f", "mpegts", "-output_ts_offset", f"{pts:.3f}", "pipe:1",
        )
        while chunk := await proc.stdout.read(CHUNK):
            out.put_nowait(chunk)
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

    def _start(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
