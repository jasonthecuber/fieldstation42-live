"""Picks the video encoder: Intel Quick Sync through VAAPI when it works, otherwise libx264."""

import functools
import logging
import os
import subprocess

_l = logging.getLogger("FS42LIVE")

MODE = os.environ.get("FS42_LIVE_HWACCEL", "auto").lower()  # auto | vaapi | none
DEVICE = os.environ.get("FS42_LIVE_VAAPI_DEVICE", "/dev/dri/renderD128")


@functools.cache
def vaapi():
    """True if segments should be encoded on the GPU. Checked once, with a one-frame test."""
    if MODE == "none":
        _l.info("video encoding: libx264 (hardware encoding turned off)")
        return False
    works = os.path.exists(DEVICE) and _test_encode()
    if works:
        _l.info(f"video encoding: h264_vaapi on {DEVICE}")
    else:
        log = _l.warning if MODE == "vaapi" else _l.info
        log(f"video encoding: libx264 (VAAPI on {DEVICE} isn't available)")
    return works


def input_args():
    return ["-vaapi_device", DEVICE] if vaapi() else []


def _test_encode():
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-vaapi_device", DEVICE,
             "-f", "lavfi", "-i", "color=s=64x64", "-frames:v", "1",
             "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
