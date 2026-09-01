"""Nova HQ ingest API.

POST /upload/{device_id}/{date}            — one segment (audio + meta), multipart
POST /upload/{device_id}/{date}/manifest    — end-of-day manifest, verifies
                                               everything arrived intact, then
                                               drops a READY marker for the
                                               processor container to pick up.
GET  /health
"""
import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

API_KEY = os.environ.get("INGEST_API_KEY", "change-me")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

app = FastAPI(title="nova-hq-ingest")


def check_key(x_api_key: str | None):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="bad or missing X-Api-Key")


def day_dir(device_id: str, date: str) -> Path:
    d = DATA_DIR / "incoming" / device_id / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/upload/{device_id}/{date}")
async def upload_segment(
    device_id: str, date: str,
    audio: UploadFile = File(...), meta: UploadFile = File(...),
    x_api_key: str | None = Header(default=None),
):
    check_key(x_api_key)
    d = day_dir(device_id, date)
    audio_bytes = await audio.read()
    meta_bytes = await meta.read()

    audio_path = d / audio.filename
    meta_path = d / meta.filename

    if audio_path.exists():
        existing_hash = sha256_bytes(audio_path.read_bytes())
        if existing_hash == sha256_bytes(audio_bytes):
            return JSONResponse(status_code=409, content={"status": "already have it"})
        # same name, different content — don't silently clobber
        raise HTTPException(status_code=409, detail="name collision with different content")

    audio_path.write_bytes(audio_bytes)
    meta_path.write_bytes(meta_bytes)
    return {"status": "ok", "bytes": len(audio_bytes)}


@app.post("/upload/{device_id}/{date}/manifest")
async def upload_manifest(device_id: str, date: str, manifest: dict,
                           x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    d = day_dir(device_id, date)

    missing, bad_hash = [], []
    for entry in manifest.get("files", []):
        p = d / entry["name"]
        if not p.exists():
            missing.append(entry["name"])
            continue
        if sha256_bytes(p.read_bytes()) != entry["sha256"]:
            bad_hash.append(entry["name"])

    if missing or bad_hash:
        raise HTTPException(status_code=422, detail={"missing": missing, "bad_hash": bad_hash})

    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (d / "READY").touch()  # signal to the processor: this day is complete
    return {"status": "ok", "files": len(manifest.get("files", []))}
