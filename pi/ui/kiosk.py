#!/usr/bin/env python3
"""Touchscreen status/control UI for the OSOYOO 3.5" SPI screen.

Full-screen tap toggles recording (sends "pause"/"resume" to the recorder
over the command FIFO). Shows a big drawn smiley (recording) / frowny
(paused) face — deliberately not relying on emoji font rendering, which is
unreliable on a small framebuffer — plus last-segment and last-upload status.

Runs standalone from any terminal for development (it'll just open a normal
window if SDL_FBDEV isn't set), and via systemd on the Pi against the real
framebuffer — see systemd/nova-recorder-ui.service and install.sh.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recorder"))
from common import load_config, read_state, send_command, DEFAULT_CONFIG_PATH  # noqa: E402

GREEN = (46, 160, 67)
GREY = (90, 90, 96)
WHITE = (240, 240, 240)
DARK = (20, 20, 24)


def find_touch_device(explicit_path):
    import evdev
    if explicit_path:
        return evdev.InputDevice(explicit_path)
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        caps = dev.capabilities().get(evdev.ecodes.EV_ABS, [])
        codes = [c for c, _ in caps]
        if evdev.ecodes.ABS_X in codes and evdev.ecodes.ABS_Y in codes:
            return dev
    return None


def draw_face(screen, w, h, recording: bool):
    import pygame
    screen.fill(GREEN if recording else GREY)
    cx, cy = w // 2, int(h * 0.42)
    r = int(min(w, h) * 0.32)
    pygame.draw.circle(screen, DARK, (cx, cy), r, width=max(4, r // 12))
    eye_dx, eye_dy = int(r * 0.42), int(r * 0.28)
    eye_r = max(4, r // 10)
    pygame.draw.circle(screen, DARK, (cx - eye_dx, cy - eye_dy), eye_r)
    pygame.draw.circle(screen, DARK, (cx + eye_dx, cy - eye_dy), eye_r)
    mouth_rect = pygame.Rect(cx - int(r * 0.55), cy + int(r * (0.05 if recording else 0.45)),
                              int(r * 1.1), int(r * 0.8))
    if recording:
        pygame.draw.arc(screen, DARK, mouth_rect, 3.66, 5.76, width=max(3, r // 14))  # smile
    else:
        pygame.draw.arc(screen, DARK, mouth_rect, 0.5, 2.6, width=max(3, r // 14))    # frown


def fmt_ago(ts):
    if not ts:
        return "never"
    s = int(time.time() - ts)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = ap.parse_args()

    cfg = load_config(args.config)
    ui_cfg = cfg["ui"]

    if os.path.exists(ui_cfg["fb_device"]):
        os.environ.setdefault("SDL_FBDEV", ui_cfg["fb_device"])
        os.environ.setdefault("SDL_VIDEODRIVER", "fbcon")
        os.environ.setdefault("SDL_NOMOUSE", "1")

    import pygame
    pygame.init()
    pygame.mouse.set_visible(False)
    w, h = ui_cfg["screen_size"]
    try:
        screen = pygame.display.set_mode((w, h))
    except pygame.error:
        # dev machine without a framebuffer / touchscreen attached
        screen = pygame.display.set_mode((w, h))
    font = pygame.font.SysFont(None, max(14, h // 16))

    touch = None
    try:
        touch = find_touch_device(ui_cfg["touch_device"])
    except Exception as e:
        print(f"[kiosk] no touch device ({e}) — running display-only")

    last_tap_state = False
    clock = pygame.time.Clock()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        tapped = False
        if touch is not None:
            import evdev
            try:
                while True:
                    ev = touch.read_one()  # non-blocking: None when no event pending
                    if ev is None:
                        break
                    if ev.type == evdev.ecodes.EV_KEY and ev.code == evdev.ecodes.BTN_TOUCH:
                        pressed = bool(ev.value)
                        if pressed and not last_tap_state:
                            tapped = True
                        last_tap_state = pressed
            except OSError as e:
                print(f"[kiosk] touch device read failed ({e}) — disabling touch")
                touch = None

        state = read_state(cfg["paths"]["state_file"])
        recording = bool(state.get("recording"))

        if tapped:
            send_command(cfg["paths"]["cmd_fifo"], "pause" if recording else "resume")

        draw_face(screen, w, h, recording)
        lines = [
            "RECORDING" if recording else "PAUSED  (tap to resume)",
            f"last segment: {fmt_ago(state.get('last_segment_at'))}",
            f"last upload: {state.get('last_upload', 'never')} "
            f"({fmt_ago(state.get('last_upload_at'))})",
        ]
        y = int(h * 0.72)
        for line in lines:
            surf = font.render(line, True, WHITE)
            screen.blit(surf, (int(w * 0.06), y))
            y += font.get_height() + 4

        pygame.display.flip()
        clock.tick(15)

    pygame.quit()


if __name__ == "__main__":
    main()
