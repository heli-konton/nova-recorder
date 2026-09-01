#!/usr/bin/env python3
"""Nova Recorder daemon.

Live mode (normal operation, needs the USB mic):
    python3 vad_recorder.py

Test mode (no mic needed — feeds a WAV file through the same segmenter/encoder
path at real-time speed, so you can prove the whole pipeline before the mic
arrives):
    python3 vad_recorder.py --input-file /path/to/test.wav

Either way, segments land in <paths.segments_dir>/<date>/ as matched
.opus + .json pairs, and /run/nova-recorder/state.json is kept current for
the kiosk UI.
"""
import argparse
import datetime
import os
import sys
import time
import wave

import numpy as np
import webrtcvad

sys.path.insert(0, os.path.dirname(__file__))
from common import (
    load_config, update_state, new_segment_id,
    open_fifo_reader_nonblocking, DEFAULT_CONFIG_PATH,
)
from segmenter import VADSegmenter
from encode import encode_opus


def day_dir(base: str) -> str:
    d = os.path.join(base, datetime.date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    return d


def write_segment(cfg: dict, seg) -> str:
    seg_id = new_segment_id()
    hhmmss = datetime.datetime.fromtimestamp(seg.start_ts).strftime("%H%M%S")
    out_dir = day_dir(cfg["paths"]["segments_dir"])
    base = f"{hhmmss}_{seg_id}"
    opus_path = os.path.join(out_dir, base + ".opus")
    json_path = os.path.join(out_dir, base + ".json")

    encode_opus(seg.pcm, seg.sample_rate, opus_path, cfg["audio"]["opus_bitrate_k"])
    import json
    with open(json_path, "w") as f:
        json.dump({
            "start_utc": datetime.datetime.fromtimestamp(seg.start_ts, datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "end_utc": datetime.datetime.fromtimestamp(seg.end_ts, datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "duration_s": round(seg.end_ts - seg.start_ts, 3),
            "sample_rate": seg.sample_rate,
        }, f)
    return opus_path


class CommandChannel:
    """Non-blocking reader for the pause/resume/stop FIFO. Safe to poll every loop."""

    def __init__(self, fifo_path: str):
        self.fifo_path = fifo_path
        self._fd = open_fifo_reader_nonblocking(fifo_path)
        self._buf = b""

    def poll(self):
        try:
            chunk = os.read(self._fd, 4096)
        except BlockingIOError:
            return []
        if not chunk:
            # writer closed; reopen so future writers still get through
            os.close(self._fd)
            self._fd = open_fifo_reader_nonblocking(self.fifo_path)
            return []
        self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        return [l.decode().strip() for l in lines if l.strip()]


def run(cfg: dict, frame_source):
    """frame_source: an iterator of (frame_bytes, timestamp) — either live
    mic frames or a WAV file chunked to the same frame size."""
    vad = webrtcvad.Vad(cfg["audio"]["vad_aggressiveness"])
    segmenter = VADSegmenter(cfg)
    cmds = CommandChannel(cfg["paths"]["cmd_fifo"])
    sample_rate = cfg["audio"]["sample_rate"]

    recording = True
    update_state(cfg["paths"]["state_file"], recording=True, last_segment=None, mode="running")

    try:
        for frame_bytes, ts in frame_source:
            for cmd in cmds.poll():
                if cmd == "pause":
                    recording = False
                    update_state(cfg["paths"]["state_file"], recording=False)
                elif cmd == "resume":
                    recording = True
                    update_state(cfg["paths"]["state_file"], recording=True)
                elif cmd == "stop":
                    raise KeyboardInterrupt

            if not recording:
                continue

            is_speech = len(frame_bytes) == int(sample_rate * cfg["audio"]["frame_ms"] / 1000) * 2 \
                and vad.is_speech(frame_bytes, sample_rate)
            finished = segmenter.push(frame_bytes, is_speech, ts)
            if finished:
                path = write_segment(cfg, finished)
                update_state(cfg["paths"]["state_file"], last_segment=path, last_segment_at=time.time())
                print(f"[nova-recorder] wrote {path}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        finished = segmenter.flush()
        if finished:
            path = write_segment(cfg, finished)
            print(f"[nova-recorder] wrote {path} (flush on shutdown)", flush=True)
        update_state(cfg["paths"]["state_file"], recording=False, mode="stopped")


def _resample_frame(pcm_bytes: bytes, in_rate: int, out_len: int) -> bytes:
    """Linear-interpolate one int16 mono PCM block to exactly out_len samples.
    Good enough for VAD gating + low-bitrate speech opus — this isn't trying
    to be audiophile-grade, just cheap and dependency-free (numpy only)."""
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return b"\x00" * (out_len * 2)
    x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.int16).tobytes()


def live_frames(cfg: dict):
    import queue
    import sounddevice as sd

    target_rate = cfg["audio"]["sample_rate"]
    frame_len = int(target_rate * cfg["audio"]["frame_ms"] / 1000)
    device = cfg["audio"]["input_device"]

    # Many USB mics don't support the pipeline's target rate as a hardware
    # rate at all (PortAudio's ALSA hostapi then fails outright with
    # "Invalid sample rate" rather than converting) — confirmed on the real
    # hardware here: a generic USB adapter that's fixed at 44100Hz, not one
    # of webrtcvad's four valid rates (8000/16000/32000/48000) anyway. So:
    # always capture at whatever the device actually reports as native, and
    # resample down to the pipeline rate before handing frames onward. When
    # a mic *does* support the target rate natively this is a no-op.
    capture_rate = int(sd.query_devices(device, "input")["default_samplerate"])
    capture_frame_len = int(capture_rate * cfg["audio"]["frame_ms"] / 1000)
    resample_needed = capture_rate != target_rate
    if resample_needed:
        print(f"[nova-recorder] mic native rate is {capture_rate}Hz, "
              f"resampling to {target_rate}Hz", flush=True)

    q: "queue.Queue[bytes]" = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(bytes(indata))

    with sd.RawInputStream(
        samplerate=capture_rate, blocksize=capture_frame_len, dtype="int16",
        channels=1, device=device, callback=callback,
    ):
        while True:
            data = q.get()
            if resample_needed:
                data = _resample_frame(data, capture_rate, frame_len)
            yield data, time.time()


def file_frames(cfg: dict, path: str, realtime: bool = True):
    """Test-mode source: chunk a WAV file into VAD-sized frames. Expects the
    WAV to already be mono at the configured sample rate (see
    scripts/generate_test_segment.py, which produces exactly that)."""
    frame_len = int(cfg["audio"]["sample_rate"] * cfg["audio"]["frame_ms"] / 1000)
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == cfg["audio"]["sample_rate"], "sample rate mismatch"
        assert wf.getnchannels() == 1, "expected mono"
        t0 = time.time()
        frame_i = 0
        while True:
            data = wf.readframes(frame_len)
            if not data:
                break
            if len(data) < frame_len * 2:
                data = data + b"\x00" * (frame_len * 2 - len(data))
            ts = t0 + frame_i * cfg["audio"]["frame_ms"] / 1000
            if realtime:
                time.sleep(cfg["audio"]["frame_ms"] / 1000)
            yield data, ts
            frame_i += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--input-file", help="WAV file to replay instead of the live mic (test mode)")
    ap.add_argument("--fast", action="store_true", help="with --input-file, don't sleep between frames")
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.input_file:
        print(f"[nova-recorder] TEST MODE — replaying {args.input_file}, no mic used")
        run(cfg, file_frames(cfg, args.input_file, realtime=not args.fast))
    else:
        run(cfg, live_frames(cfg))


if __name__ == "__main__":
    main()
