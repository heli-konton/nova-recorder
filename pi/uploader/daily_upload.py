#!/usr/bin/env python3
"""End-of-day upload to Nova HQ. Run by the nova-uploader.timer (systemd),
or by hand: `python3 daily_upload.py [--date YYYY-MM-DD] [--dry-run]`.

Idempotent: files already accepted by HQ (by sha256) are skipped on retry,
so a failed run partway through is safe to just re-run.
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recorder"))
from common import load_config, update_state, DEFAULT_CONFIG_PATH  # noqa: E402
from encode import sha256_file  # noqa: E402


def build_manifest(day_dir: str, device_id: str, date: str) -> dict:
    files = []
    for opus_path in sorted(glob.glob(os.path.join(day_dir, "*.opus"))):
        json_path = opus_path[:-5] + ".json"
        # A segment interrupted mid-write (service restarted, power blip) can leave a
        # zero-byte .opus and/or an empty/corrupt .json behind. That's a normal failure
        # mode for an always-on appliance, not something worth aborting the whole day's
        # upload over — skip just that segment and keep going. (Hit for real 2026-08-30:
        # three segments left empty by rapid systemctl stop/start during mic testing.)
        if os.path.getsize(opus_path) == 0:
            print(f"  skipping {os.path.basename(opus_path)}: empty audio file "
                  f"(likely an interrupted write)")
            continue
        meta = {}
        if os.path.exists(json_path):
            try:
                with open(json_path) as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"  skipping {os.path.basename(opus_path)}: unreadable metadata "
                      f"({os.path.basename(json_path)}: {e})")
                continue
        files.append({
            "name": os.path.basename(opus_path),
            "sha256": sha256_file(opus_path),
            "duration_s": meta.get("duration_s"),
            "start_utc": meta.get("start_utc"),
            "end_utc": meta.get("end_utc"),
        })
    return {"device_id": device_id, "date": date, "files": files}


def upload_one(hq_url: str, api_key: str, device_id: str, date: str,
                day_dir: str, fname: str, attempts: int, backoff_s: int) -> bool:
    url = f"{hq_url}/upload/{device_id}/{date}"
    opus_path = os.path.join(day_dir, fname)
    json_path = opus_path[:-5] + ".json"
    for attempt in range(1, attempts + 1):
        try:
            with open(opus_path, "rb") as audio_f, open(json_path, "rb") as meta_f:
                resp = requests.post(
                    url,
                    files={
                        "audio": (fname, audio_f, "audio/opus"),
                        "meta": (os.path.basename(json_path), meta_f, "application/json"),
                    },
                    headers={"X-Api-Key": api_key},
                    timeout=60,
                )
            if resp.status_code in (200, 201, 409):  # 409 = HQ already has it
                return True
            print(f"  {fname}: HQ returned {resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as e:
            print(f"  {fname}: attempt {attempt}/{attempts} failed: {e}")
        if attempt < attempts:
            time.sleep(backoff_s)
    return False


def send_manifest(hq_url: str, api_key: str, device_id: str, date: str, manifest: dict) -> bool:
    url = f"{hq_url}/upload/{device_id}/{date}/manifest"
    try:
        resp = requests.post(url, json=manifest, headers={"X-Api-Key": api_key}, timeout=30)
        return resp.status_code in (200, 201)
    except requests.RequestException as e:
        print(f"  manifest POST failed: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--date", help="YYYY-MM-DD, default = today")
    ap.add_argument("--dry-run", action="store_true",
                     help="build the manifest and print it, don't contact Nova HQ")
    args = ap.parse_args()

    cfg = load_config(args.config)
    date = args.date or __import__("datetime").date.today().isoformat()
    day_dir = os.path.join(cfg["paths"]["segments_dir"], date)

    if not os.path.isdir(day_dir) or not glob.glob(os.path.join(day_dir, "*.opus")):
        print(f"[nova-uploader] nothing to upload for {date} ({day_dir})")
        return 0

    manifest = build_manifest(day_dir, cfg["device_id"], date)
    print(f"[nova-uploader] {len(manifest['files'])} segment(s) for {date}")

    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        print("[nova-uploader] dry-run: not contacting Nova HQ")
        return 0

    up = cfg["upload"]
    ok = True
    for f in manifest["files"]:
        print(f"  uploading {f['name']} ...")
        if not upload_one(up["hq_url"], up["api_key"], cfg["device_id"], date,
                           day_dir, f["name"], up["retry_attempts"], up["retry_backoff_s"]):
            ok = False
            print(f"  {f['name']}: giving up after {up['retry_attempts']} attempts")

    if not ok:
        update_state(cfg["paths"]["state_file"], last_upload="failed", last_upload_at=time.time())
        print("[nova-uploader] one or more segments failed — leaving files in place, will retry next run")
        return 1

    if not send_manifest(up["hq_url"], up["api_key"], cfg["device_id"], date, manifest):
        update_state(cfg["paths"]["state_file"], last_upload="manifest_failed", last_upload_at=time.time())
        print("[nova-uploader] all segments uploaded but manifest confirmation failed — will retry next run")
        return 1

    if up["delete_after_upload"]:
        shutil.rmtree(day_dir)
    else:
        dest = os.path.join(cfg["paths"]["uploaded_dir"], date)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.move(day_dir, dest)

    update_state(cfg["paths"]["state_file"], last_upload="ok", last_upload_at=time.time())
    print(f"[nova-uploader] {date} fully uploaded and confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
