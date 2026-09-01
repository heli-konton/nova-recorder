#!/usr/bin/env bash
# Idempotent bootstrap for the Nova Recorder Pi. Run as root (sudo) from
# wherever you've copied this repo, e.g.:
#   scp -r nova-recorder pi@<pi-host>:/tmp/
#   ssh pi@<pi-host>
#   sudo /tmp/nova-recorder/pi/install.sh
#
# Safe to re-run — every step checks before it acts.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR=/opt/nova-recorder

if [[ "$REPO_ROOT" != "$INSTALL_DIR" ]]; then
  echo "==> Copying repo to $INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  rsync -a --delete --exclude venv "$REPO_ROOT"/ "$INSTALL_DIR"/
  echo "==> Re-running from $INSTALL_DIR"
  exec "$INSTALL_DIR/pi/install.sh" "$@"
fi

echo "==> apt packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3-venv python3-pip python3-dev \
  ffmpeg portaudio19-dev \
  libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-ttf-2.0-0 fonts-dejavu-core \
  alsa-utils rsync git

echo "==> enabling SPI (for the touchscreen)"
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_spi 0
else
  echo "    raspi-config not found — enable SPI manually via /boot/firmware/config.txt (dtparam=spi=on)"
fi

# --- TODO: OSOYOO 3.5" "HD Capi Screen" (2024009100) display driver -------
# This screen ships with its own install script / dtoverlay (QR code on the
# box points to it). Generic ILI9486/XPT2046 overlays are NOT guaranteed to
# match this exact revision. Before the screen will show anything:
#   1. Scan the QR code on the box (or check osoyoo.com for "2024009100")
#      and run their install script, OR find the matching dtoverlay line.
#   2. Confirm it creates /dev/fb1 (`ls /dev/fb*` after reboot) and a touch
#      input device (`sudo evtest` to list, or `python3 -c "import evdev;
#      print(evdev.list_devices())"`).
#   3. Only then enable nova-recorder-ui.service (below) — the recorder and
#      uploader work fine without the screen if this needs more time.
# ---------------------------------------------------------------------------

echo "==> creating 'nova' service user"
if ! id nova >/dev/null 2>&1; then
  useradd --system --create-home --groups audio,video,input,tty,dialout nova
fi

echo "==> creating data directories"
install -d -o nova -g nova /var/lib/nova-recorder/segments /var/lib/nova-recorder/uploaded
install -d -o nova -g nova /etc/nova-recorder

if [[ ! -f /etc/nova-recorder/config.yaml ]]; then
  cp "$INSTALL_DIR/pi/config.example.yaml" /etc/nova-recorder/config.yaml
  chown nova:nova /etc/nova-recorder/config.yaml
  echo "    wrote /etc/nova-recorder/config.yaml from the example — EDIT IT (device_id, upload.hq_url, upload.api_key)"
else
  echo "    /etc/nova-recorder/config.yaml already exists, leaving it alone"
fi

echo "==> python venv"
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
  python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/pi/requirements.txt"
chown -R nova:nova "$INSTALL_DIR"

echo "==> systemd units"
cp "$INSTALL_DIR"/pi/systemd/*.service "$INSTALL_DIR"/pi/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable nova-recorder.service nova-uploader.timer
echo "    nova-recorder-ui.service installed but NOT enabled yet — enable once"
echo "    the screen driver (see TODO above) is confirmed working:"
echo "      sudo systemctl enable --now nova-recorder-ui.service"

echo "==> Tailscale"
if ! command -v tailscale >/dev/null; then
  echo "    Tailscale not found. Install + join with:"
  echo "      curl -fsSL https://tailscale.com/install.sh | sh"
  echo "      sudo tailscale up"
else
  if tailscale status >/dev/null 2>&1; then
    echo "    Tailscale already up: $(tailscale ip -4 2>/dev/null || true)"
  else
    echo "    Tailscale installed but not connected — run: sudo tailscale up"
  fi
fi

cat <<EOF

==> Done. What's left before this is live:
  1. Edit /etc/nova-recorder/config.yaml (device_id, upload.hq_url, upload.api_key
     — api_key must match hq/.env on Nova HQ)
  2. Sort the screen driver (TODO above), then enable nova-recorder-ui.service
  3. Plug in the USB mic, find its name: $INSTALL_DIR/venv/bin/python3 -c "import sounddevice as sd; print(sd.query_devices())"
     and set audio.input_device in the config if it's not the default
  4. Start it: sudo systemctl start nova-recorder.service
  5. Before then, you can prove the whole pipeline with no mic at all:
     $INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/pi/scripts/generate_test_segment.py
     $INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/pi/recorder/vad_recorder.py --input-file /tmp/nova_test.wav --fast
     $INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/pi/uploader/daily_upload.py --dry-run
EOF
