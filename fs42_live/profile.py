import logging
from dataclasses import dataclass

_l = logging.getLogger("FS42LIVE")


@dataclass(frozen=True)
class OutputProfile:
    """The normalized format every source encodes to, so a channel is one continuous stream."""

    width: int = 640
    height: int = 480
    fps: str = "30000/1001"
    video_bitrate: str = "1500k"
    video_maxrate: str = "2000k"
    audio_bitrate: str = "128k"
    audio_rate: int = 48000

    def video_filter(self, hw=False, overlay=None, crop_to=None):
        # Square the pixels first (DVD rips are often anamorphic), then fit inside the
        # frame: widescreen gets letterboxed, 4:3 fills it with no bars. The scaling stays
        # on the CPU either way; with hw the frames are then handed to the GPU encoder.
        # An overlay (captions) is drawn on the finished frame. With crop_to (a station's
        # `live_crop_to`, e.g. "16:9"), a picture wider than that loses its sides down to it
        # first, as TV's pan and scan did: "4:3" fills the screen, "14:9" or "16:9" keep thin bars.
        w, h = self.width, self.height
        aspect = _aspect(crop_to)
        return (
            "scale=trunc(iw*sar/2)*2:ih,setsar=1,"
            + (f"crop=w='min(iw,trunc(ih*{aspect:.4f}/2)*2)':h=ih," if aspect else "") +
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            + (f"{overlay}," if overlay else "") +
            f"fps={self.fps}," + ("format=nv12,hwupload" if hw else "format=yuv420p")
        )

    def encode_args(self, hw=False):
        # ~1 s: a new viewer never waits long for a keyframe, and clients that get HLS from
        # Jellyfin (Roku) start sooner, since Jellyfin can only cut its segments at keyframes
        gop = 30
        if hw:
            video = ["-c:v", "h264_vaapi", "-bf", "0"]
        else:
            video = ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                     "-keyint_min", str(gop), "-sc_threshold", "0"]
        return [
            *video, "-profile:v", "main",
            "-b:v", self.video_bitrate, "-maxrate", self.video_maxrate,
            "-bufsize", self.video_maxrate, "-g", str(gop),
            "-c:a", "aac", "-b:a", self.audio_bitrate,
            "-ar", str(self.audio_rate), "-ac", "2",
        ]


def _aspect(value):
    """ "16:9" or 1.85 as a width-to-height ratio; None for none (or one that makes no sense)."""
    if not value:
        return None
    try:
        a, _, b = str(value).partition(":")
        ratio = float(a) / float(b) if b else float(a)
    except (ValueError, ZeroDivisionError):
        ratio = 0
    if not 1 <= ratio <= 3:
        _l.warning(f'live_crop_to {value!r} isn\'t a shape like "16:9"; not cropping')
        return None
    return ratio


RETRO = OutputProfile()
