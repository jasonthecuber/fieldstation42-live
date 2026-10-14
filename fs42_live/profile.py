from dataclasses import dataclass


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

    def video_filter(self, hw=False):
        # Square the pixels first (DVD rips are often anamorphic), then fit inside the
        # frame: widescreen gets letterboxed, 4:3 fills it with no bars. The scaling stays
        # on the CPU either way; with hw the frames are then handed to the GPU encoder.
        w, h = self.width, self.height
        return (
            "scale=trunc(iw*sar/2)*2:ih,setsar=1,"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={self.fps}," + ("format=nv12,hwupload" if hw else "format=yuv420p")
        )

    def encode_args(self, hw=False):
        gop = 60  # ~2 s, so a new viewer never waits long for a keyframe
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


RETRO = OutputProfile()
