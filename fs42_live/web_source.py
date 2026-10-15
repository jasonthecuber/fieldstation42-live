import asyncio
import collections
import logging
import os
import random
import shutil
import tempfile
import urllib.parse
from asyncio.subprocess import DEVNULL, PIPE

from fs42.station_manager import StationManager
from fs42_live import hwaccel
from fs42_live.profile import RETRO

_l = logging.getLogger("FS42LIVE")

AUDIO_EXTS = {".mp3", ".ogg", ".wav", ".flac", ".aac", ".m4a", ".opus"}
FIRST_DISPLAY = 100  # Xvfb display = this + channel number, so channels never share one
DISPLAY_WAIT = 5.0  # seconds to wait for Xvfb to come up
PAGE_LOAD = 1.0  # let the page draw before capture starts (measured: 0.5 s for the guide, 1 s for WS4KP)
CHUNK = 64 * 1024


class WebPageSource:
    """One live MPEG-TS stream of a web page (`web` stations: the retro guide, weather).

    The page is rendered by Chromium on its own virtual display (Xvfb) and captured with
    x11grab. Chromium is muted; the soundtrack is the music folder named by the page's
    `music` URL parameter (as upstream's custom guide uses it) or the station's
    `live_music`, shuffled and looped, or silence if there is none.
    """

    def __init__(self, station_conf, profile=RETRO):
        self.station = station_conf
        self.network = station_conf["network_name"]
        self.profile = profile
        self.url = self._full_url(station_conf["web_url"])
        self.display = f":{FIRST_DISPLAY + station_conf['channel_number']}"
        self._procs = []
        self._tail = collections.deque(maxlen=5)  # last lines from Xvfb and Chromium, for errors

    async def stream(self):
        """Yield MPEG-TS bytes for as long as the caller keeps reading."""
        p = self.profile
        workdir = tempfile.mkdtemp(prefix="fs42-web-")
        try:
            self._clear_stale_display()
            await self._spawn("Xvfb", "Xvfb", self.display, "-screen", "0", f"{p.width}x{p.height}x24",
                              "-nolisten", "tcp")
            await self._wait_for_display()
            browser = await self._spawn(
                "chromium", "chromium", "--no-sandbox", "--kiosk", "--no-first-run", "--noerrdialogs",
                "--disable-infobars", "--disable-dev-shm-usage", "--disable-gpu", "--hide-scrollbars",
                "--mute-audio", "--force-device-scale-factor=1", "--window-position=0,0",
                f"--window-size={p.width},{p.height}", f"--user-data-dir={workdir}/profile", self.url,
            )
            await asyncio.sleep(PAGE_LOAD)

            hw = await asyncio.to_thread(hwaccel.vaapi)
            audio_in, audio_map = self._audio_input(workdir)
            _l.info(f"{self.network}: capturing {self.url}")
            encoder = await self._spawn(
                "ffmpeg", "ffmpeg", "-hide_banner", "-loglevel", "error",
                *hwaccel.input_args(),
                "-f", "x11grab", "-draw_mouse", "0", "-framerate", p.fps,
                "-video_size", f"{p.width}x{p.height}", "-i", self.display,
                *audio_in,
                "-map", "0:v", "-map", audio_map,
                "-vf", p.video_filter(hw), "-af", "aresample=async=1",
                *p.encode_args(hw),
                "-f", "mpegts", "pipe:1",
                stdout=PIPE,
            )
            # a crashed browser would otherwise leave the channel showing a frozen black screen
            watchdog = asyncio.create_task(self._end_with(browser, encoder))
            _l.info(f"{self.network}: stream started")
            try:
                while chunk := await encoder.stdout.read(CHUNK):
                    yield chunk
            finally:
                watchdog.cancel()
        finally:
            for proc in reversed(self._procs):  # encoder, then browser, then Xvfb
                if proc.returncode is None:
                    proc.terminate()  # lets Xvfb remove its lock file
            for proc in self._procs:
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except asyncio.TimeoutError:
                    proc.kill()
            shutil.rmtree(workdir, ignore_errors=True)
            _l.info(f"{self.network}: stream stopped")

    def _audio_input(self, workdir):
        tracks = self._music_tracks()
        if not tracks:
            p = self.profile
            return ["-f", "lavfi", "-i", f"anullsrc=r={p.audio_rate}:cl=stereo"], "1:a"
        random.shuffle(tracks)
        playlist = os.path.join(workdir, "music.txt")
        with open(playlist, "w") as f:
            for track in tracks:
                escaped = track.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
        return ["-re", "-stream_loop", "-1", "-f", "concat", "-safe", "0", "-i", playlist], "1:a:0"

    def _music_tracks(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.url).query)
        folder = self.station.get("live_music") or (query.get("music") or [None])[0]
        if not folder:
            return []
        folder = os.path.abspath(folder.lstrip("/"))  # relative to the FS42 folder, like the media API
        try:
            return [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                    if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
        except OSError as e:
            _l.warning(f"{self.network}: no music ({e}); using silence")
            return []

    @staticmethod
    def _full_url(url):
        if url.startswith("/"):  # a page on our own server
            return StationManager().local_server_url() + url
        return url

    def _clear_stale_display(self):
        # Xvfb killed uncleanly (say, by a container restart) leaves these behind, and a new
        # one then refuses the display. The display number is this channel's alone.
        n = self.display[1:]
        for path in (f"/tmp/.X{n}-lock", f"/tmp/.X11-unix/X{n}"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    async def _wait_for_display(self):
        socket = f"/tmp/.X11-unix/X{self.display[1:]}"
        for _ in range(int(DISPLAY_WAIT / 0.1)):
            if self._procs[0].returncode is not None:
                break
            if os.path.exists(socket):
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"Xvfb didn't start on {self.display}: {list(self._tail)}")

    async def _end_with(self, browser, encoder):
        await browser.wait()
        if encoder.returncode is None:
            _l.error(f"{self.network}: browser exited ({browser.returncode}); ending the stream. "
                     f"Last output: {list(self._tail)}")
            encoder.kill()

    async def _spawn(self, tag, *args, stdout=DEVNULL):
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=DEVNULL, stdout=stdout, stderr=PIPE,
            env={**os.environ, "DISPLAY": self.display},
        )
        self._procs.append(proc)
        asyncio.create_task(self._drain_log(proc, tag))
        return proc

    async def _drain_log(self, proc, tag):
        # stderr must be read, or a chatty process blocks on a full pipe. Chromium is
        # very chatty about missing hardware it doesn't need, so its output is only kept for errors.
        while line := await proc.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            if tag == "ffmpeg":
                _l.warning(f"{self.network} ffmpeg: {text}")
            else:
                self._tail.append(f"{tag}: {text}")
        await proc.wait()
