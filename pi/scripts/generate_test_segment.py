#!/usr/bin/env python3
"""Synthesizes a mono 16kHz WAV with a few bursts of "speech" (actually
speech-band noise, which webrtcvad reliably flags as voiced) separated by
silence, so vad_recorder.py --input-file can be exercised end-to-end with no
mic attached. This is the thing to run today; swap in the real mic tomorrow
by dropping --input-file.

Usage:
    python3 generate_test_segment.py [output.wav]
"""
import struct
import sys
import wave

import numpy as np

SAMPLE_RATE = 16000


def speech_like_burst(duration_s: float, rng: np.random.Generator) -> np.ndarray:
    n = int(duration_s * SAMPLE_RATE)
    # band-limited noise ~300-3000Hz reads as "speech" to a VAD, unlike pure silence/tone
    noise = rng.normal(0, 1, n)
    t = np.arange(n) / SAMPLE_RATE
    carrier = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 540 * t)
    sig = 0.6 * noise * 0.3 + 0.4 * carrier
    envelope = np.ones(n)
    fade = min(200, n // 4)
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[-fade:] = np.linspace(1, 0, fade)
    sig = sig * envelope
    return (sig * 0.5 * 32767).astype(np.int16)


def silence(duration_s: float) -> np.ndarray:
    return np.zeros(int(duration_s * SAMPLE_RATE), dtype=np.int16)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/nova_test.wav"
    rng = np.random.default_rng(42)

    chunks = [
        silence(0.5),
        speech_like_burst(2.0, rng),
        silence(1.5),
        speech_like_burst(3.5, rng),
        silence(1.5),                  # > end_silence_ms default (1200ms), so this is a real gap
        speech_like_burst(0.8, rng),   # short one, should still pass min_segment_ms
        silence(2.0),
    ]
    audio = np.concatenate(chunks)

    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(struct.pack(f"<{len(audio)}h", *audio.tolist()))

    print(f"wrote {out_path} ({len(audio) / SAMPLE_RATE:.1f}s, expect 3 segments)")


if __name__ == "__main__":
    main()
