import os, time, sys, subprocess
from PIL import Image, ImageDraw
import numpy as np

FB_PATH = "/dev/fb0"
WIDTH, HEIGHT = 480, 320

def get_stats():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = float(f.read().strip()) / 1000.0
            temp_str = f"{temp:.1f}°C"
    except Exception:
        temp_str = "N/A"

    try:
        ip = subprocess.check_output(["hostname", "-I"]).decode().strip().split()[0]
    except Exception:
        ip = "100.84.73.87"

    try:
        with open("/proc/uptime") as f:
            up_sec = float(f.read().split()[0])
            mins = int(up_sec // 60)
            hours = mins // 60
            mins = mins % 60
            uptime_str = f"{hours}h {mins}m"
    except Exception:
        uptime_str = "N/A"

    return temp_str, ip, uptime_str

def render_dash():
    temp_str, ip, uptime = get_stats()

    img = Image.new("RGB", (WIDTH, HEIGHT), color=(18, 20, 28))
    draw = ImageDraw.Draw(img)

    # Header bar
    draw.rectangle([(0, 0), (WIDTH, 40)], fill=(35, 39, 56))
    draw.text((15, 12), "REMEMBERER PI  [NovaHQ]", fill=(0, 210, 255))
    draw.text((370, 12), "v1.0", fill=(140, 150, 180))

    # Recording Status Card
    draw.rectangle([(15, 55), (465, 130)], fill=(28, 32, 48), outline=(0, 210, 255), width=2)
    draw.ellipse([(35, 77), (65, 107)], fill=(255, 45, 85))
    draw.text((80, 72), "RECORDER STATUS", fill=(160, 170, 200))
    draw.text((80, 95), "AUDIO CAPTURE ACTIVE", fill=(255, 255, 255))

    # System Stats Cards
    draw.rectangle([(15, 145), (155, 235)], fill=(28, 32, 48), outline=(60, 70, 95), width=1)
    draw.text((25, 155), "CPU TEMP", fill=(140, 150, 180))
    draw.text((25, 185), temp_str, fill=(255, 180, 0))

    draw.rectangle([(170, 145), (310, 235)], fill=(28, 32, 48), outline=(60, 70, 95), width=1)
    draw.text((180, 155), "TAILSCALE IP", fill=(140, 150, 180))
    draw.text((180, 185), ip, fill=(0, 220, 130))

    draw.rectangle([(325, 145), (465, 235)], fill=(28, 32, 48), outline=(60, 70, 95), width=1)
    draw.text((335, 155), "UPTIME", fill=(140, 150, 180))
    draw.text((335, 185), uptime, fill=(200, 130, 255))

    # Footer
    draw.rectangle([(0, 285), (WIDTH, HEIGHT)], fill=(25, 28, 40))
    draw.text((15, 292), "Heli & Dan • NovaHQ Recorder Gremlin (=^･ω･^=)", fill=(120, 130, 160))

    arr = np.array(img, dtype=np.uint16)
    r = (arr[:, :, 0] >> 3) & 0x1F
    g = (arr[:, :, 1] >> 2) & 0x3F
    b = (arr[:, :, 2] >> 3) & 0x1F
    rgb565 = (r << 11) | (g << 5) | b
    raw_bytes = rgb565.astype("<u2").tobytes()

    if os.path.exists(FB_PATH):
        with open(FB_PATH, "wb") as fb:
            fb.write(raw_bytes)

if __name__ == "__main__":
    while True:
        try:
            render_dash()
        except Exception as e:
            print("Dash error:", e)
        time.sleep(3)
