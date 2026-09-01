# Nova Recorder

All-day desk recorder → Nova HQ → daily notes/todos/summary.
Read **ARCHITECTURE.md** first — it has the full design and every assumption
made while building this.

## What's built vs. what's left

**Built and smoke-tested today (no mic needed):**
- VAD segmenter with pre-roll (`pi/recorder/segmenter.py`) — verified with
  synthetic audio, correctly splits/merges segments around silence gaps
- Opus encoding + sha256 (`pi/recorder/encode.py`)
- Recorder daemon with a `--input-file` test mode and live-mic mode sharing
  the same code path (`pi/recorder/vad_recorder.py`)
- Pause/resume/stop control via a FIFO (`pi/recorder/common.py`)
- Uploader with idempotent retry + manifest confirmation
  (`pi/uploader/daily_upload.py`) — dry-run tested
- Touchscreen kiosk UI for the OSOYOO 3.5" screen (`pi/ui/kiosk.py`)
- systemd units for all three services + nightly upload timer
- `install.sh` — idempotent bootstrap for the Pi
- Nova HQ ingest API (`hq/ingest/`) — tested with a FastAPI TestClient:
  upload, duplicate-detection, bad-key rejection, manifest verification all
  pass
- Nova HQ processing scaffold (`hq/process/pipeline.py`) — real control flow
  and prompt, not yet run (needs model downloads + HF/Anthropic keys, see
  below)

**Genuinely blocked on the mic:**
- Live capture (`vad_recorder.py` with no `--input-file`) — can't verify
  real speech detection, or pick the right `audio.input_device`, without it
  plugged in
- Everything past that is just "run it and see" once the mic exists

**Not done today (deliberately out of scope, see ARCHITECTURE.md):**
- Speaker enrollment / cross-day identity (diarization gives consistent
  labels *within* a day, not named people)
- Retention/privacy policy for raw audio
- The enclosure

## Getting this onto the Pi

1. `scp -r nova-recorder pi@<pi-host>:/tmp/`
2. `ssh pi@<pi-host>`
3. `sudo /tmp/nova-recorder/pi/install.sh`
4. Follow the printed next-steps (edit config, sort the screen driver, run
   the no-mic smoke test, then plug in the mic tomorrow)

## Getting Nova HQ running

1. On the homelab box: copy `hq/` over, `cp .env.example .env` and fill it in
2. `docker compose build && docker compose up -d`
3. Confirm the tailnet can reach it: `curl http://<hq-tailscale-host>:8770/health`
4. The `processor` container downloads whisperX + pyannote model weights on
   first run — that's the slow part, and needs `HF_TOKEN` (for the gated
   pyannote diarization model — accept its terms on Hugging Face first) and
   `ANTHROPIC_API_KEY` set beforehand
