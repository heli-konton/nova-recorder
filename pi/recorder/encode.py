"""PCM -> Opus via ffmpeg (already on the Pi via install.sh)."""
import hashlib
import subprocess


def encode_opus(pcm_bytes: bytes, sample_rate: int, out_path: str, bitrate_k: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
        "-c:a", "libopus", "-b:a", f"{bitrate_k}k",
        out_path,
    ]
    proc = subprocess.run(cmd, input=pcm_bytes, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg opus encode failed: {proc.stderr.decode(errors='replace')}")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
