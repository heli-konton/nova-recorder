# Nova Recorder — Architecture

An always-on desk recorder (Pi 5 + SSD + USB mic + OSOYOO 3.5" SPI touchscreen)
that captures speech all day, uploads to **Nova HQ** (homelab box, over
Tailscale) each night, and gets turned into notes / to-dos / a daily summary.

## Assumptions locked in this pass

These were decided together; anything not decided is called out as a TODO.

| Decision | Choice |
|---|---|
| Where does audio land? | Nova HQ — an existing Docker host on your tailnet, reached via its Tailscale hostname |
| Where does transcription + speaker ID happen? | At Nova HQ, after upload — Pi never runs a model |
| Capture policy | VAD-gated continuous — always listening, only speech (+ pre-roll) hits disk |
| Today's build scope | Full Pi software stack + HQ ingest/processing scaffold + touchscreen kiosk UI, stopping right before the mic is needed |

**Assumptions made without a follow-up question** (flag if wrong, easy to change):

- **Pi state**: SD card not yet flashed, SSD attaches via a HAT (per your note) —
  `install.sh` assumes a fresh Raspberry Pi OS Lite (64-bit, Bookworm) booting
  from the SSD, SD card only used to get it alive long enough to clone to SSD
  (or skipped if you image the SSD directly with Pi Imager).
- **Touchscreen**: confirmed from your photos — **OSOYOO 3.5" SPI touchscreen,
  "HD Capi Screen" variant (model 2024009100)**. That's a GPIO-header SPI
  display with capacitive touch, *not* the 7" DSI panel. Two consequences:
  - It needs OSOYOO's own display driver / dtoverlay installed (their box has
    a QR code linking to the setup script — `install.sh` has a clearly marked
    TODO for the exact overlay line since it's vendor/version-specific).
  - It sits on the 40-pin GPIO header. **If your SSD HAT also uses the 40-pin
    header (rather than the PCIe/M.2 slot directly), the two physically
    collide.** Worth a 30-second check before you 3D print anything — a
    stacking header or moving the screen to a short GPIO ribbon cable both fix
    it if so.
- **How this gets onto the Pi**: I don't have a bridge to the Pi itself (only
  to your Mac, and no folder's connected there yet), so this is delivered as
  a repo you `scp`/copy over and run `install.sh` on, rather than me driving
  it live. Say the word once the Pi's reachable some other way and we can do
  it live instead.
- **Tailscale**: Pi joins your tailnet (matches your existing homelab
  pattern) — HQ is addressed by its Tailscale hostname, not a LAN IP.

## Data flow

```
 [USB mic] → vad_recorder.py → segments (.opus + .json) → daily_upload.py
                                        │                        │
                                 /var/lib/nova-recorder    nightly, over
                                   /segments/<date>/          Tailscale
                                                                 │
                                                                 ▼
                                                    Nova HQ :8770  ingest API
                                                    /data/incoming/<date>/
                                                                 │
                                                        processor picks up
                                                       (whisperX + pyannote
                                                        diarization + LLM)
                                                                 │
                                                                 ▼
                                              /data/processed/<date>/
                                                transcript.json
                                                notes.md
                                                todos.md
                                                summary.md
```

The touchscreen UI (`ui/kiosk.py`) doesn't sit in this pipeline — it just
reads/writes a small state file and a command FIFO to show status and let you
start/stop/pause.

## On-Pi layout

```
/etc/nova-recorder/config.yaml          # single config file, see pi/config.example.yaml
/var/lib/nova-recorder/segments/<date>/ # today's not-yet-uploaded segments
/var/lib/nova-recorder/uploaded/<date>/ # archived after a confirmed upload
/run/nova-recorder/state.json           # live status: recording?, last segment, last upload
/run/nova-recorder/cmd.fifo             # kiosk UI -> recorder: pause | resume | stop
```

## Segment format

Each speech segment (pre-roll + speech + trailing silence) becomes:

- `HHMMSS_<uuid8>.opus` — mono, 16kHz source, encoded to Opus 24kbps (speech
  is intelligible well below that; a full day of near-continuous talk is
  still only tens of MB)
- `HHMMSS_<uuid8>.json` — `{start_utc, end_utc, duration_s, sample_rate}`

## Upload contract (Pi → Nova HQ)

`POST /upload/{device_id}/{date}` — multipart, one segment file + its sidecar
JSON per call, `X-Api-Key` header.

`POST /upload/{device_id}/{date}/manifest` — final call for the day: full
manifest (`{files: [{name, sha256, duration_s}], device_id, date}`). HQ
verifies every listed file arrived intact, then queues processing. This is
the "day is done, go process it" signal — so the uploader can be re-run
safely if it fails partway (segments already received are skipped by hash).

## Processing pipeline (HQ)

1. `whisperx` transcribes each segment (word-level timestamps).
2. `pyannote.audio` diarizes each segment into `SPEAKER_00`, `SPEAKER_01`, ...
   — **not yet identity-matched to named people or across days.** Real
   "knows different people" needs a voice-embedding enrollment step (record
   a short labelled clip per person once, match new segments against those
   embeddings). Flagged as the natural next milestone, not built today —
   diarization alone (consistent labels *within* a day) is the honest v1.
3. Segments are stitched into one time-ordered transcript for the day.
4. Transcript → Claude API with a prompt asking for a plain summary, a
   to-do list (things you said you'd do / were asked to do), and freeform
   notes. Output written as three markdown files.

## Open items for later (not blocking today)

- Enclosure: 3D-printed, sits in front of you on the desk. No constraints
  captured yet beyond "fits the Pi 5 + HAT + screen + mic" — worth doing
  after you've physically test-fit the SSD HAT + screen stack-up above.
- Speaker enrollment / cross-day identity.
- Privacy/retention policy (how long raw audio is kept after processing —
  currently: forever, in `uploaded/`. You'll want an opinion on this before
  this thing runs for a month.)
