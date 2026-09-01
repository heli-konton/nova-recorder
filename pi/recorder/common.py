"""Shared helpers: config loading, atomic state file, FIFO control channel."""
import json
import os
import stat
import tempfile
import time
import uuid

import yaml

DEFAULT_CONFIG_PATH = "/etc/nova-recorder/config.yaml"


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    for d in (cfg["paths"]["segments_dir"], cfg["paths"]["uploaded_dir"]):
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.dirname(cfg["paths"]["state_file"]), exist_ok=True)
    return cfg


def atomic_write_json(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def read_state(state_file: str) -> dict:
    try:
        with open(state_file, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def update_state(state_file: str, **kwargs) -> dict:
    state = read_state(state_file)
    state.update(kwargs)
    state["updated_at"] = time.time()
    atomic_write_json(state_file, state)
    return state


def ensure_fifo(fifo_path: str) -> None:
    if os.path.exists(fifo_path):
        if not stat.S_ISFIFO(os.stat(fifo_path).st_mode):
            os.remove(fifo_path)
        else:
            return
    os.makedirs(os.path.dirname(fifo_path), exist_ok=True)
    os.mkfifo(fifo_path)


def send_command(cmd_fifo: str, command: str) -> None:
    """Non-blocking-ish write from the UI side. Opens, writes one line, closes."""
    ensure_fifo(cmd_fifo)
    fd = os.open(cmd_fifo, os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(fd, (command.strip() + "\n").encode())
    finally:
        os.close(fd)


def open_fifo_reader_nonblocking(fifo_path: str) -> int:
    """Opens the FIFO for reading without blocking, even with no writer yet."""
    ensure_fifo(fifo_path)
    return os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)


def new_segment_id() -> str:
    return uuid.uuid4().hex[:8]
