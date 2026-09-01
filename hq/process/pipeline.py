"""Nova HQ processor.

Polls /data/incoming/<device>/<date>/READY, and for each unprocessed day:
  1. decode each .opus segment to wav
  2. transcribe with whisperX (word-level timestamps)
  3. diarize with pyannote (SPEAKER_00, SPEAKER_01, ... — not yet matched to
     named people or across days; see ARCHITECTURE.md)
  4. stitch every segment's transcript into one time-ordered document for
     the day, using each segment's start_utc from its meta json as the
     offset
  5. send that transcript to Claude for a summary / to-do list / notes
  6. write everything to /data/processed/<device>/<date>/

This is a scaffold: the control flow, file contracts, and prompt are real,
but it needs HF_TOKEN (pyannote gated model access) and ANTHROPIC_API_KEY
set in hq/.env before it does anything, and the first run downloads several
GB of model weights. Nothing here needs to work today — the Pi doesn't
depend on it to record.
"""
import datetime
import json
import os
import subprocess
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "60"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
DIARIZE = os.environ.get("DIARIZE", "true").lower() == "true"
HF_TOKEN = os.environ.get("HF_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

SUMMARY_PROMPT = """You are given a raw, speaker-labelled transcript of one \
person's day, captured from a desk microphone. Speaker labels like \
SPEAKER_00 are not reliable identities — treat the primary/most frequent \
speaker as the device owner unless context clearly says otherwise.

Produce three sections, each starting with a line "=== NAME ===":

=== SUMMARY ===
A few paragraphs of what actually happened today, in plain language.

=== TODOS ===
A markdown checklist of concrete action items the owner said they'd do, or \
were asked to do. Skip this section's content (just the header) if there \
genuinely aren't any.

=== NOTES ===
Anything else worth remembering: decisions made, names/facts mentioned, \
loose ends. Bullet points.

Transcript:
{transcript}
"""


def find_pending_days():
    incoming = DATA_DIR / "incoming"
    if not incoming.exists():
        return []
    pending = []
    for device_dir in incoming.iterdir():
        if not device_dir.is_dir():
            continue
        for date_dir in device_dir.iterdir():
            ready = date_dir / "READY"
            done = DATA_DIR / "processed" / device_dir.name / date_dir.name / "DONE"
            if ready.exists() and not done.exists():
                pending.append((device_dir.name, date_dir.name, date_dir))
    return pending


def decode_to_wav(opus_path: Path, wav_path: Path):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(opus_path),
         "-ar", "16000", "-ac", "1", str(wav_path)],
        check=True,
    )


def transcribe_segment(wav_path: Path, whisper_model, diarize_pipeline):
    """Returns a list of {start_s, end_s, text, speaker} relative to the
    start of this segment."""
    import whisperx

    audio = whisperx.load_audio(str(wav_path))
    result = whisper_model.transcribe(audio, batch_size=8)
    model_a, metadata = whisperx.load_align_model(language_code=result["language"], device=WHISPER_DEVICE)
    result = whisperx.align(result["segments"], model_a, metadata, audio, WHISPER_DEVICE)

    lines = []
    if diarize_pipeline is not None:
        diarization = diarize_pipeline(str(wav_path))
        result_ds = whisperx.assign_word_speakers(diarization, result)
        segments = result_ds["segments"]
    else:
        segments = result["segments"]

    for seg in segments:
        lines.append({
            "start_s": seg["start"],
            "end_s": seg["end"],
            "text": seg["text"].strip(),
            "speaker": seg.get("speaker", "SPEAKER_00"),
        })
    return lines


def stitch_day(device_id: str, date: str, day_dir: Path, whisper_model, diarize_pipeline):
    manifest = json.loads((day_dir / "manifest.json").read_text())
    transcript_lines = []

    for entry in sorted(manifest["files"], key=lambda e: e.get("start_utc") or e["name"]):
        opus_path = day_dir / entry["name"]
        wav_path = day_dir / (entry["name"] + ".wav")
        decode_to_wav(opus_path, wav_path)

        seg_lines = transcribe_segment(wav_path, whisper_model, diarize_pipeline)
        seg_start = datetime.datetime.fromisoformat(entry["start_utc"].rstrip("Z"))
        for line in seg_lines:
            ts = seg_start + datetime.timedelta(seconds=line["start_s"])
            transcript_lines.append({
                "time": ts.isoformat() + "Z",
                "speaker": line["speaker"],
                "text": line["text"],
            })
        wav_path.unlink(missing_ok=True)

    transcript_lines.sort(key=lambda l: l["time"])
    return transcript_lines


def render_transcript_for_prompt(lines):
    return "\n".join(f"[{l['time']}] {l['speaker']}: {l['text']}" for l in lines)


def summarize(transcript_text: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(transcript=transcript_text)}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")

    sections = {"SUMMARY": "", "TODOS": "", "NOTES": ""}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("=== ") and stripped.endswith(" ==="):
            name = stripped.strip("= ").strip()
            if name in sections:
                current = name
                continue
        if current:
            sections[current] += line + "\n"
    return sections


def process_day(device_id: str, date: str, day_dir: Path, whisper_model, diarize_pipeline):
    print(f"[processor] processing {device_id}/{date}")
    out_dir = DATA_DIR / "processed" / device_id / date
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = stitch_day(device_id, date, day_dir, whisper_model, diarize_pipeline)
    (out_dir / "transcript.json").write_text(json.dumps(lines, indent=2))

    transcript_text = render_transcript_for_prompt(lines)
    sections = summarize(transcript_text)
    (out_dir / "summary.md").write_text(sections["SUMMARY"].strip() + "\n")
    (out_dir / "todos.md").write_text(sections["TODOS"].strip() + "\n")
    (out_dir / "notes.md").write_text(sections["NOTES"].strip() + "\n")

    (out_dir / "DONE").touch()
    print(f"[processor] done: {out_dir}")


def main():
    print("[processor] loading whisperX model (first run downloads weights)...")
    import whisperx
    whisper_model = whisperx.load_model(WHISPER_MODEL, WHISPER_DEVICE, compute_type="int8")

    diarize_pipeline = None
    if DIARIZE:
        if not HF_TOKEN:
            print("[processor] DIARIZE=true but HF_TOKEN not set — skipping diarization")
        else:
            from pyannote.audio import Pipeline
            diarize_pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1", use_auth_token=HF_TOKEN)

    print(f"[processor] watching {DATA_DIR}/incoming every {POLL_INTERVAL_S}s")
    while True:
        for device_id, date, day_dir in find_pending_days():
            try:
                process_day(device_id, date, day_dir, whisper_model, diarize_pipeline)
            except Exception as e:
                print(f"[processor] FAILED {device_id}/{date}: {e}")
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
