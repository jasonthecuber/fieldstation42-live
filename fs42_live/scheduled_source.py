import asyncio
import datetime
import hashlib
import logging
import os
import random
import shutil
import time
from asyncio.subprocess import DEVNULL, PIPE
from dataclasses import dataclass

from fs42.catalog_api import CatalogAPI
from fs42.liquid_manager import LiquidManager
from fs42_live import bars, captions, hwaccel, logos, loudness, messages, panscan
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
STILLS = "runtime/stills"  # still pictures (the standby slate) encoded once, then streamed by copying
STILL_SECONDS = 20  # each is encoded this long, and looped for a longer slot


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
            refresh_soon()
            left = entry.duration - pp.offset
            stand_in = self._stand_in(entry, left)
            if stand_in:
                # a commercial or bumper deleted: another of the channel's fills its time, so the break
                # keeps its length and the show comes back on time
                _l.info(f"{self.network}: {entry.path} is missing; playing {stand_in.path} in its place")
                return Segment(stand_in.path, 0, min(left, stand_in.duration))
            # deleted (or its drive is gone); the catalog watcher catches up, then the
            # schedule moves on. Until then, short slates so it switches over quickly
            _l.warning(f"{self.network}: {entry.path} is missing; showing standby")
            return self._slate(min(SLATE_SECONDS, left))
        offset = max(0.0, pp.offset - NUDGE.total_seconds())
        return Segment(entry.path, entry.skip + offset, entry.duration - offset)

    def _stand_in(self, entry, seconds):
        """Another of the channel's commercials (or bumpers, for a bumper) to play in a missing one's
        place: one at least as long, so it fills the time, or else the longest, and another after it."""
        if getattr(entry, "content_type", None) not in ("commercial", "bump"):
            return None
        try:
            have = [e for e in CatalogAPI.get_entries(self.station)
                    if e.content_type == entry.content_type and e.duration >= 1 and os.path.exists(e.path)]
        except Exception:
            return None
        if not have:
            return None
        long_enough = [e for e in have if e.duration >= seconds]
        return random.choice(long_enough) if long_enough else max(have, key=lambda e: e.duration)

    def _slate(self, seconds):
        image = self.station.get("standby_image", "runtime/standby.png")
        return Segment(image if os.path.exists(image) else None, 0, seconds)

    async def _encode(self, seg, pts, out):
        """Encode one segment into out, falling back to the slate if ffmpeg fails."""
        try:
            began = time.monotonic()
            if await self._run_encode(seg, pts, out):
                return
            if seg.path and os.path.splitext(seg.path)[1].lower() not in IMAGE_EXTS:
                refresh_soon()  # renamed under us, or the schedule was rebuilt elsewhere
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
        overlay, pan, pan_file = None, "", None
        if seg.path is None:
            video_in = ["-f", "lavfi", "-i", f"color=c=black:s={p.width}x{p.height}:r={p.fps}"]
            has_audio = False
        elif os.path.splitext(seg.path)[1].lower() in IMAGE_EXTS:
            video_in = ["-loop", "1", "-framerate", p.fps, "-i", seg.path]
            has_audio = False
        else:
            video_in = ["-ss", f"{seg.start:.3f}", "-i", seg.path]
            has_audio = await self._has_audio(seg.path)
            if captions.wanted(self.station, seg.path):
                overlay = captions.drawtext(seg.path, seg.start, seg.start + seg.duration)
            if self.station.get("live_crop_to"):
                pan, pan_file = panscan.pan_filter(seg.path, seg.start, seg.duration)

        overlay = messages.join(overlay, messages.crawl_filter(self.station["channel_number"], pts))
        hw = await asyncio.to_thread(hwaccel.vaapi)
        vf = p.video_filter(hw, overlay, self.station.get("live_crop_to"), bars.crop_filter(seg.path),
                            logos.logo_filter(seg.path), pan)
        audio_in = ["-f", "lavfi", "-i", f"anullsrc=r={p.audio_rate}:cl=stereo"]
        still = None
        if (seg.path is None or os.path.splitext(seg.path)[1].lower() in IMAGE_EXTS) and overlay is None:
            # nothing moves in a still picture, so it needn't be encoded again every time; on a busy box
            # (many channels on the slate at once) that is most of the work
            still = await self._still_file(seg.path, [*video_in, *audio_in], vf, hw)
        _l.info(f"{self.network}: {seg.path} from {seg.start:.1f}s for {seg.duration:.1f}s"
                + (" (encoded before)" if still else ""))
        try:
            if still:
                proc = await self._spawn(
                    os.path.basename(seg.path or "slate"),
                    "-re", "-stream_loop", "-1", "-i", still, "-t", f"{seg.duration:.3f}",
                    "-map", "0", "-c", "copy", "-f", "mpegts", "-output_ts_offset", f"{pts:.3f}", "pipe:1",
                )
            else:
                proc = await self._spawn(
                    os.path.basename(seg.path or "slate"),
                    *hwaccel.input_args(), "-re", *video_in, *audio_in,
                    "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a",
                    "-t", f"{seg.duration:.3f}", "-shortest",
                    "-vf", vf, "-af", loudness.audio_filter(seg.path),
                    *p.encode_args(hw),
                    "-f", "mpegts", "-output_ts_offset", f"{pts:.3f}", "pipe:1",
                )
            while chunk := await proc.stdout.read(CHUNK):
                out.put_nowait(chunk)
            return await proc.wait() == 0
        finally:
            if pan_file:
                os.remove(pan_file)

    _stills_making = {}  # file: the task making it
    _stills_failed = set()  # not tried again until the server restarts

    async def _still_file(self, path, inputs, vf, hw):
        """The still picture as this channel encodes it, kept in STILLS once made; None until then (the
        caller encodes it live meanwhile). It is made in the background, at low priority, only once."""
        args = [*hwaccel.input_args(), *inputs, "-map", "0:v:0", "-map", "1:a", "-t", str(STILL_SECONDS),
                "-vf", vf, *self.profile.encode_args(hw), "-f", "mpegts"]
        try:
            stamp = os.stat(path).st_mtime_ns if path else 0
        except OSError:
            return None
        out = os.path.join(STILLS, hashlib.sha1(repr((path, stamp, args)).encode()).hexdigest()[:16] + ".ts")
        if os.path.exists(out):
            return out
        if out not in self._stills_making and out not in self._stills_failed:
            # not in _tasks: it outlives this stream
            self._stills_making[out] = asyncio.create_task(self._make_still(path, args, out))
        return None

    async def _make_still(self, path, args, out):
        tmp = f"{out}.{os.getpid()}.part"
        try:
            os.makedirs(STILLS, exist_ok=True)
            nice = ["nice", "-n", "10"] if shutil.which("nice") else []
            proc = await asyncio.create_subprocess_exec(
                *nice, "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, tmp,
                stdin=DEVNULL, stdout=DEVNULL, stderr=PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp):
                os.replace(tmp, out)
                _l.info(f"{self.network}: {path or 'the slate'} encoded once, for every channel to copy")
            else:
                self._stills_failed.add(out)
                _l.warning(f"{self.network}: couldn't encode {path or 'the slate'} ahead; it is encoded each time: "
                           f"{err.decode(errors='replace').strip()[-300:]}")
        except Exception:
            self._stills_failed.add(out)
            _l.exception(f"{self.network}: couldn't encode {path or 'the slate'} ahead")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
            self._stills_making.pop(out, None)

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
