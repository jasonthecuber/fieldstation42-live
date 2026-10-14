"""Just enough MPEG-TS parsing to start a new viewer on a video keyframe."""

PACKET = 188
VIDEO_STREAM_TYPES = {0x01, 0x02, 0x1B, 0x24}  # MPEG-1/2, H.264, HEVC
MAX_GOP_BYTES = 8 * 1024 * 1024  # if keyframes stop being found, don't grow forever


def _pid(pkt):
    return ((pkt[1] & 0x1F) << 8) | pkt[2]


def _payload_offset(pkt):
    control = (pkt[3] >> 4) & 0x3
    if not control & 0x1:
        return None
    offset = 4 + (1 + pkt[4] if control & 0x2 else 0)
    return offset if offset < PACKET else None


def _section(pkt):
    """The PSI section that starts in this packet, if one does."""
    offset = _payload_offset(pkt)
    if offset is None or not pkt[1] & 0x40:
        return None
    return pkt[offset + 1 + pkt[offset]:]


def _random_access(pkt):
    # the muxer flags each video keyframe's first packet in its adaptation field
    return (pkt[3] & 0x20) and pkt[4] > 0 and pkt[5] & 0x40


class GopCache:
    """The stream since the latest video keyframe, preceded by the current PAT and PMT.

    Fed whole packets as they go out; a new viewer gets a snapshot first, so they can
    decode from their first byte instead of waiting up to a GOP for the next keyframe.
    """

    def __init__(self):
        self._pat = self._pmt = None
        self._pmt_pid = self._video_pid = None
        self._gop = []
        self._size = 0

    def feed(self, data):
        keyframe_at = None
        for i in range(0, len(data), PACKET):
            pkt = data[i:i + PACKET]
            pid = _pid(pkt)
            if pid == 0:
                self._read_pat(pkt)
            elif pid == self._pmt_pid:
                self._read_pmt(pkt)
            elif pid == self._video_pid and _random_access(pkt):
                keyframe_at = i
        if keyframe_at is not None:
            self._gop, self._size = [data[keyframe_at:]], len(data) - keyframe_at
        elif self._gop:
            self._gop.append(data)
            self._size += len(data)
            if self._size > MAX_GOP_BYTES:
                self._gop, self._size = [], 0

    def snapshot(self):
        if not (self._gop and self._pat and self._pmt):
            return []
        return [b"".join([self._pat, self._pmt, *self._gop])]

    def _read_pat(self, pkt):
        s = _section(pkt)
        if not s or s[0] != 0x00:
            return
        end = 3 + (((s[1] & 0x0F) << 8) | s[2]) - 4  # less the CRC
        for j in range(8, end, 4):
            if (s[j] << 8) | s[j + 1]:  # program 0 is the network PID, not a PMT
                self._pmt_pid = ((s[j + 2] & 0x1F) << 8) | s[j + 3]
                self._pat = pkt
                return

    def _read_pmt(self, pkt):
        s = _section(pkt)
        if not s or s[0] != 0x02:
            return
        end = 3 + (((s[1] & 0x0F) << 8) | s[2]) - 4
        j = 12 + (((s[10] & 0x0F) << 8) | s[11])
        while j + 5 <= end:
            if s[j] in VIDEO_STREAM_TYPES:
                self._video_pid = ((s[j + 1] & 0x1F) << 8) | s[j + 2]
                self._pmt = pkt
                return
            j += 5 + (((s[j + 3] & 0x0F) << 8) | s[j + 4])
