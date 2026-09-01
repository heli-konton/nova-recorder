"""Source-agnostic VAD segmenter: feed it (frame_bytes, is_speech) pairs from
*any* origin (a live mic callback or a WAV file read in chunks) and it yields
finished speech segments with pre-roll applied. This is what lets the whole
pipeline be smoke-tested before the USB mic exists — see scripts/generate_test_segment.py.
"""
import collections
import time
from dataclasses import dataclass, field


@dataclass
class FinishedSegment:
    pcm: bytes
    start_ts: float
    end_ts: float
    sample_rate: int
    frame_ms: int


class VADSegmenter:
    def __init__(self, cfg: dict):
        a = cfg["audio"]
        self.sample_rate = a["sample_rate"]
        self.frame_ms = a["frame_ms"]
        self.min_segment_ms = a["min_segment_ms"]
        pre_roll_frames = max(1, a["pre_roll_ms"] // self.frame_ms)
        end_silence_frames = max(1, a["end_silence_ms"] // self.frame_ms)

        self._pre_roll = collections.deque(maxlen=pre_roll_frames)
        self._end_silence_frames = end_silence_frames
        self._triggered = False
        self._voiced_frames: list[bytes] = []
        self._silence_run = 0
        self._segment_start_ts = None

    def push(self, frame_bytes: bytes, is_speech: bool, ts: float | None = None):
        """Feed one frame. Returns a FinishedSegment when a segment just closed,
        else None."""
        ts = ts if ts is not None else time.time()

        if not self._triggered:
            self._pre_roll.append((frame_bytes, ts))
            if is_speech:
                self._triggered = True
                self._segment_start_ts = self._pre_roll[0][1]
                self._voiced_frames = [f for f, _ in self._pre_roll]
                self._pre_roll.clear()
                self._silence_run = 0
            return None

        # already triggered: accumulate, watch for end-of-speech
        self._voiced_frames.append(frame_bytes)
        if is_speech:
            self._silence_run = 0
        else:
            self._silence_run += 1
            if self._silence_run >= self._end_silence_frames:
                return self._finish(ts)
        return None

    def flush(self, ts: float | None = None):
        """Force-close whatever segment is in progress (e.g. on shutdown)."""
        if self._triggered:
            return self._finish(ts if ts is not None else time.time())
        return None

    def _finish(self, end_ts: float):
        duration_ms = len(self._voiced_frames) * self.frame_ms
        seg = None
        if duration_ms >= self.min_segment_ms:
            seg = FinishedSegment(
                pcm=b"".join(self._voiced_frames),
                start_ts=self._segment_start_ts,
                end_ts=end_ts,
                sample_rate=self.sample_rate,
                frame_ms=self.frame_ms,
            )
        self._triggered = False
        self._voiced_frames = []
        self._silence_run = 0
        self._segment_start_ts = None
        return seg
