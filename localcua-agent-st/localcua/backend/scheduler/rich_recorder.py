"""
rich_recorder.py
----------------
Records the user's live screen actions as "Rich Action Packets" —
the structured format that gives the GUI Agent everything it needs to
faithfully re-execute a task from a fresh screen state.

Each packet contains four layers:
    1. Visual Context   — before screenshot, element crop, after screenshot
    2. Semantic Meta    — window title, process name, accessibility label, URL
    3. Temporal Data    — precise timing, inter-action deltas, idle detection
    4. Intent           — OCR text under/around the cursor at click time

Install (pip install):
    pynput              cross-platform mouse + keyboard listener
    Pillow              screenshots and image crops
    psutil              process name from PID
    pygetwindow         active window title + bounds       (Windows / macOS)
    pywinauto           accessibility tree                 (Windows only)
    atomacos            accessibility tree                 (macOS only)
    python-xlib         active window info                 (Linux / X11)
    pytesseract         OCR for intent text                (optional)
    apscheduler         scheduling (used by scheduler.py)

All third-party imports have graceful fallbacks — the recorder works with
only pynput + Pillow, enriching packets with whatever else is available.

Output format:  JSON, one file per session
    {
      "session_id":    "2025-04-05T10-30-00",
      "recorded_at":  "2025-04-05T10:30:00",
      "platform":     "Windows",
      "screen_width":  1920,
      "screen_height": 1080,
      "packets": [
        {
          "seq":            1,
          "action_type":    "click",
          "button":         "left",
          "x":              500,
          "y":              200,
          "t_abs":          "2025-04-05T10:30:01.123",
          "t_offset_s":     1.123,
          "t_delta_s":      0.0,          <- time since previous action
          "idle_wait_s":    0.0,          <- non-zero if user paused > threshold

          "visual": {
            "before_b64":   "<base64 PNG>",   <- full screen before action
            "crop_b64":     "<base64 PNG>",   <- 224×224 crop centred on click
            "after_b64":    "<base64 PNG>"    <- full screen after action
          },

          "semantic": {
            "window_title": "Inbox - user@gmail.com - Google Chrome",
            "process_name": "chrome.exe",
            "process_pid":  12345,
            "ctrl_name":    "Compose",        <- accessibility label
            "ctrl_role":    "Button",
            "active_url":   "https://mail.google.com/mail/u/0/#inbox",
            "window_rect":  [0, 0, 1920, 1080]
          },

          "temporal": {
            "t_offset_s":   1.123,
            "t_delta_s":    0.821,
            "idle_wait_s":  0.0,
            "recommended_wait_s": 0.5    <- suggested post-action wait for agent
          },

          "intent": {
            "text_under_cursor": "Compose",
            "text_nearby":       "Compose  Inbox  Starred  Sent",
            "ocr_region_b64":    "<base64 PNG>"  <- the region that was OCR'd
          }
        },
        ...
      ]
    }
"""

# Purpose: Records user GUI actions into rich packets with screenshots, timing, and metadata.

from __future__ import annotations

import base64
import io
import json
import logging
import platform
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Optional dependency imports — each wrapped so the recorder degrades cleanly
# ─────────────────────────────────────────────────────────────────────────────

# pynput — mouse + keyboard listeners
try:
    from pynput import keyboard as _kb
    from pynput import mouse as _ms
    _PYNPUT = True
except ImportError:
    _PYNPUT = False
    logger.warning("pynput not installed — pip install pynput")

# Pillow — screenshots and crops
try:
    from PIL import Image, ImageGrab
    _PIL = True
except ImportError:
    _PIL = False
    logger.warning("Pillow not installed — pip install Pillow")

# psutil — process name from PID
try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

# ── Windows ──────────────────────────────────────────────────────────────────
_WINDOWS = platform.system() == "Windows"
try:
    import pygetwindow as gw   # type: ignore
    _PYGETWINDOW = True
except ImportError:
    _PYGETWINDOW = False

try:
    import pywinauto            # type: ignore
    from pywinauto import Desktop as _WinDesktop
    _PYWINAUTO = True
except ImportError:
    _PYWINAUTO = False

# ── macOS ────────────────────────────────────────────────────────────────────
_MACOS = platform.system() == "Darwin"
try:
    import atomacos             # type: ignore
    _ATOMACOS = True
except ImportError:
    _ATOMACOS = False

# ── Linux / X11 ──────────────────────────────────────────────────────────────
_LINUX = platform.system() == "Linux"
try:
    from Xlib import display as _XDisplay, X as _X
    _XLIB = True
except ImportError:
    _XLIB = False

# ── OCR (optional) ───────────────────────────────────────────────────────────
try:
    import pytesseract          # type: ignore
    _TESSERACT = True
except ImportError:
    _TESSERACT = False


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

CROP_SIZE          = 224        # pixels — element crop around click point
IDLE_THRESHOLD_S   = 2.0        # pause longer than this → log as idle_wait
MOVE_DEDUPE_S      = 0.15       # drop hover events faster than this
TEXT_REGION_PAD    = 120        # pixels each side around click for OCR region
AFTER_SHOT_DELAY_S = 0.35       # wait before capturing the "after" screenshot
JPEG_QUALITY       = 75         # balance size vs clarity for embedded images
CLICK_JPEG_QUALITY = 70         # slightly smaller JPEGs for click packets


def _enable_windows_dpi_awareness() -> None:
    """
    Make this process DPI-aware on Windows so mouse coordinates and screenshots
    stay in the same coordinate space when display scaling is enabled.
    """
    if not _WINDOWS:
        return

    try:
        import ctypes

        # Prefer per-monitor awareness when available.
        try:
            shcore = ctypes.windll.shcore
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            # E_ACCESSDENIED means awareness is already set by the process.
            result = shcore.SetProcessDpiAwareness(2)
            if result not in (0, 0x80070005):
                logger.debug("SetProcessDpiAwareness returned non-zero code: %s", result)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception as exc:
        logger.debug("Unable to set Windows DPI awareness: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Image helpers
# ─────────────────────────────────────────────────────────────────────────────

def _grab_screen() -> Optional["Image.Image"]:
    """Capture the full screen. Returns PIL Image or None."""
    if not _PIL:
        return None
    try:
        return ImageGrab.grab()
    except Exception as exc:
        logger.debug("Screen grab failed: %s", exc)
        return None


def _img_to_b64(img: "Image.Image", fmt: str = "JPEG", quality: int = JPEG_QUALITY) -> str:
    """Encode a PIL image to a base64 string."""
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
    else:
        img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _crop_around(img: "Image.Image", x: int, y: int, size: int = CROP_SIZE) -> "Image.Image":
    """Crop a square region of `size`×`size` centred on (x, y)."""
    half = size // 2
    left   = max(0, x - half)
    top    = max(0, y - half)
    right  = min(img.width,  left + size)
    bottom = min(img.height, top  + size)
    return img.crop((left, top, right, bottom))


def _ocr_region(
    img: "Image.Image", x: int, y: int, pad: int = TEXT_REGION_PAD, quality: int = JPEG_QUALITY
) -> Tuple[str, str, Optional[str]]:
    """
    Return (text_under_cursor, text_nearby, region_b64).
    text_under_cursor is the word nearest (x, y).
    text_nearby is all OCR text in the padded region.
    region_b64 is the cropped region as base64 (for the packet).
    """
    left   = max(0, x - pad)
    top    = max(0, y - pad)
    right  = min(img.width,  x + pad)
    bottom = min(img.height, y + pad)
    region = img.crop((left, top, right, bottom))
    region_b64 = _img_to_b64(region, "JPEG", quality=quality)

    if not _TESSERACT:
        return ("", "", region_b64)

    try:
        data = pytesseract.image_to_data(
            region, output_type=pytesseract.Output.DICT, lang="eng"
        )
        words   = [w for w in data["text"] if w.strip()]
        nearby  = "  ".join(words)

        # Find the word whose bounding box centre is closest to (x - left, y - top)
        rx, ry  = x - left, y - top
        best_w  = ""
        best_d  = float("inf")
        for i, word in enumerate(data["text"]):
            if not word.strip():
                continue
            cx = data["left"][i] + data["width"][i]  // 2
            cy = data["top"][i]  + data["height"][i] // 2
            d  = (cx - rx) ** 2 + (cy - ry) ** 2
            if d < best_d:
                best_d, best_w = d, word
        return (best_w, nearby, region_b64)
    except Exception as exc:
        logger.debug("OCR failed: %s", exc)
        return ("", "", region_b64)


# ─────────────────────────────────────────────────────────────────────────────
# Semantic helpers — window / process / accessibility / URL
# ─────────────────────────────────────────────────────────────────────────────

def _active_window_info() -> Dict[str, Any]:
    """
    Return dict with window_title, process_name, process_pid, window_rect.
    Works across Windows / macOS / Linux with graceful fallbacks.
    """
    info: Dict[str, Any] = {
        "window_title": "",
        "process_name": "",
        "process_pid":  None,
        "window_rect":  None,
        "active_url":   "",
        "ctrl_name":    "",
        "ctrl_role":    "",
    }

    # ── Windows ──────────────────────────────────────────────────────────────
    if _WINDOWS:
        if _PYGETWINDOW:
            try:
                win = gw.getActiveWindow()
                if win:
                    info["window_title"] = win.title or ""
                    info["window_rect"]  = [win.left, win.top, win.width, win.height]
            except Exception as exc:
                logger.debug("pygetwindow error: %s", exc)

        # Process name from HWND via pywinauto
        if _PYWINAUTO:
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                pid  = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                info["process_pid"] = pid.value
                if _PSUTIL:
                    p = psutil.Process(pid.value)
                    info["process_name"] = p.name()
            except Exception as exc:
                logger.debug("Win32 PID error: %s", exc)

        # Accessibility label + role via pywinauto
        if _PYWINAUTO:
            try:
                desktop = _WinDesktop(backend="uia")
                focused = desktop.get_focus()
                info["ctrl_name"] = getattr(focused, "Name", "") or ""
                info["ctrl_role"] = getattr(focused, "friendly_class_name", lambda: "")() or ""
            except Exception as exc:
                logger.debug("pywinauto focus error: %s", exc)

    # ── macOS ─────────────────────────────────────────────────────────────────
    elif _MACOS:
        try:
            result = subprocess.run(
                [
                    "osascript", "-e",
                    'tell application "System Events" to get name of first process '
                    'whose frontmost is true'
                ],
                capture_output=True, text=True, timeout=2
            )
            info["process_name"] = result.stdout.strip()
        except Exception as exc:
            logger.debug("osascript error: %s", exc)

        try:
            result = subprocess.run(
                [
                    "osascript", "-e",
                    'tell application "System Events" to get title of front window '
                    'of first process whose frontmost is true'
                ],
                capture_output=True, text=True, timeout=2
            )
            info["window_title"] = result.stdout.strip()
        except Exception:
            pass

        if _ATOMACOS:
            try:
                app      = atomacos.getAppRefByBundleId(
                    atomacos.getFrontmostApp().bundleIdentifier()
                )
                focused  = app.AXFocusedUIElement
                info["ctrl_name"] = getattr(focused, "AXTitle", "") or ""
                info["ctrl_role"] = getattr(focused, "AXRole",  "") or ""
            except Exception as exc:
                logger.debug("atomacos error: %s", exc)

    # ── Linux / X11 ──────────────────────────────────────────────────────────
    elif _LINUX:
        if _XLIB:
            try:
                d   = _XDisplay.Display()
                win = d.get_input_focus().focus
                # Walk up to find _NET_WM_NAME
                while win:
                    try:
                        name = win.get_full_text_property(
                            d.intern_atom("_NET_WM_NAME")
                        )
                        if name:
                            info["window_title"] = name if isinstance(name, str) else name.decode(errors="replace")
                            break
                    except Exception:
                        pass
                    try:
                        win = win.query_tree().parent
                    except Exception:
                        break

                # _NET_WM_PID
                win = d.get_input_focus().focus
                try:
                    pid_prop = win.get_full_property(
                        d.intern_atom("_NET_WM_PID"), _X.AnyPropertyType
                    )
                    if pid_prop:
                        pid = pid_prop.value[0]
                        info["process_pid"] = pid
                        if _PSUTIL:
                            p = psutil.Process(pid)
                            info["process_name"] = p.name()
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("Xlib error: %s", exc)
        else:
            # Fallback: xdotool (if available on PATH)
            try:
                wid  = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True, text=True, timeout=2
                ).stdout.strip()
                name = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, text=True, timeout=2
                ).stdout.strip()
                info["window_title"] = name

                pid_out = subprocess.run(
                    ["xdotool", "getwindowpid", wid],
                    capture_output=True, text=True, timeout=2
                ).stdout.strip()
                if pid_out.isdigit():
                    info["process_pid"] = int(pid_out)
                    if _PSUTIL:
                        p = psutil.Process(int(pid_out))
                        info["process_name"] = p.name()
            except FileNotFoundError:
                pass   # xdotool not installed
            except Exception as exc:
                logger.debug("xdotool error: %s", exc)

    # ── URL: check Chrome / Firefox via subprocess (all platforms) ────────────
    info["active_url"] = _get_browser_url(info.get("process_name", ""))

    return info


def _get_browser_url(process_name: str) -> str:
    """
    Attempt to retrieve the current browser URL.
    Works for Chrome on macOS/Windows; Firefox requires additional setup.
    """
    pn = (process_name or "").lower()
    if not any(b in pn for b in ("chrome", "chromium", "firefox", "msedge", "brave")):
        return ""

    if _MACOS:
        try:
            script = (
                'tell application "Google Chrome" to get URL of active tab of front window'
                if "chrome" in pn else
                'tell application "Firefox" to get URL of active tab of front window'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2
            )
            return result.stdout.strip()
        except Exception:
            return ""

    if _WINDOWS and _PYWINAUTO:
        try:
            desktop = _WinDesktop(backend="uia")
            for win in desktop.windows():
                if "chrome" in win.window_text().lower() or "firefox" in win.window_text().lower():
                    addr_bar = win.child_window(auto_id="addressEditBox")
                    return addr_bar.get_value()
        except Exception:
            pass

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Rich Action Packet builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_packet(
    seq:          int,
    action_type:  str,
    t_offset_s:   float,
    t_delta_s:    float,
    idle_wait_s:  float,
    extra:        Dict[str, Any],
    before_img:   Optional["Image.Image"],
    capture_after: bool = True,
    jpeg_quality: int = JPEG_QUALITY,
) -> Dict[str, Any]:
    """
    Assemble one Rich Action Packet.

    `extra` carries action-specific fields (x, y, button, text, keys…).
    """
    x = extra.get("x")
    y = extra.get("y")

    # ── Visual layer ──────────────────────────────────────────────────────────
    visual: Dict[str, Any] = {}
    if before_img and _PIL:
        visual["before_b64"] = _img_to_b64(before_img, quality=jpeg_quality)
        if x is not None and y is not None:
            crop = _crop_around(before_img, int(x), int(y))
            visual["crop_b64"] = _img_to_b64(crop, "PNG")   # PNG for crops (lossless)

    if capture_after and _PIL:
        time.sleep(AFTER_SHOT_DELAY_S)
        after_img = _grab_screen()
        if after_img:
            visual["after_b64"] = _img_to_b64(after_img, quality=jpeg_quality)

    # ── Semantic layer ────────────────────────────────────────────────────────
    semantic = _active_window_info()

    # ── Intent layer ──────────────────────────────────────────────────────────
    intent: Dict[str, Any] = {}
    if before_img and x is not None and y is not None:
        text_under, text_nearby, region_b64 = _ocr_region(before_img, int(x), int(y), quality=jpeg_quality)
        intent["text_under_cursor"] = text_under
        intent["text_nearby"]       = text_nearby
        if region_b64:
            intent["ocr_region_b64"]  = region_b64

    # ── Recommended post-action wait ─────────────────────────────────────────
    # If the user waited a long time after this action, the agent should too.
    # We suggest waiting at least AFTER_SHOT_DELAY_S or up to the user's idle.
    recommended_wait = max(AFTER_SHOT_DELAY_S, min(idle_wait_s, 10.0))

    packet: Dict[str, Any] = {
        "seq":         seq,
        "action_type": action_type,
        "t_abs":       datetime.now().isoformat(timespec="milliseconds"),
        "t_offset_s":  round(t_offset_s, 3),
        "t_delta_s":   round(t_delta_s,  3),
        "idle_wait_s": round(idle_wait_s, 3),
        **extra,
        "visual":   visual,
        "semantic": semantic,
        "temporal": {
            "t_offset_s":          round(t_offset_s, 3),
            "t_delta_s":           round(t_delta_s,  3),
            "idle_wait_s":         round(idle_wait_s, 3),
            "recommended_wait_s":  round(recommended_wait, 3),
        },
        "intent": intent,
    }
    return packet


# ─────────────────────────────────────────────────────────────────────────────
# RichRecorder
# ─────────────────────────────────────────────────────────────────────────────

class RichRecorder:
    """
    Records the user's live actions as Rich Action Packets.

    Each packet includes:
        visual   — before screenshot, element crop, after screenshot
        semantic — window title, process name, accessibility label, URL
        temporal — timestamps, deltas, idle detection
        intent   — OCR text under and around the click point

    Usage:
        rec = RichRecorder()
        rec.start()
        input("Perform your task, then press Enter...")
        session = rec.stop()
        rec.save("sessions/my_task.json")
    """

    def __init__(
        self,
        capture_after_shot:   bool  = True,
        capture_screenshots:  bool  = True,
        run_ocr:              bool  = True,
        capture_semantics:    bool  = True,
        crop_size:            int   = CROP_SIZE,
        idle_threshold_s:     float = IDLE_THRESHOLD_S,
        move_dedupe_s:        float = MOVE_DEDUPE_S,
    ) -> None:
        if not _PYNPUT:
            raise RuntimeError("pynput is required.  pip install pynput")
        if not _PIL:
            raise RuntimeError("Pillow is required.  pip install Pillow")

        _enable_windows_dpi_awareness()

        self.capture_after_shot  = capture_after_shot
        self.capture_screenshots = capture_screenshots
        self.run_ocr             = run_ocr
        self.capture_semantics   = capture_semantics
        self.crop_size           = crop_size
        self.idle_threshold_s    = idle_threshold_s
        self.move_dedupe_s       = move_dedupe_s

        self._packets:    List[Dict[str, Any]] = []
        self._seq:        int   = 0
        self._running:    bool  = False
        self._lock:       threading.Lock = threading.Lock()

        self._start_time:    float = 0.0
        self._last_action_t: float = 0.0
        self._last_move_t:   float = 0.0

        # Accumulate typed characters into one type() packet
        self._pending_text:   str   = ""
        self._pending_text_t: float = 0.0
        self._held_modifiers: set   = set()

        self._kb_listener:    Any = None
        self._mouse_listener: Any = None

        self._screen_w: int = 1920
        self._screen_h: int = 1080

    # ─────────────────────────────────────────────────────────── public API

    def start(self) -> None:
        """Begin recording in background threads."""
        if self._running:
            logger.warning("RichRecorder is already running.")
            return

        if _PIL:
            try:
                screen = _grab_screen()
                if screen:
                    self._screen_w, self._screen_h = screen.size
            except Exception:
                pass

        self._packets       = []
        self._seq           = 0
        self._start_time    = time.monotonic()
        self._last_action_t = self._start_time
        self._running       = True
        self._pending_text  = ""
        self._held_modifiers.clear()

        self._mouse_listener = _ms.Listener(
            on_click=self._on_click,
            on_scroll=self._on_scroll,
            on_move=self._on_move,
        )
        self._kb_listener = _kb.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_listener.start()
        self._kb_listener.start()
        logger.info("RichRecorder started — screen %dx%d", self._screen_w, self._screen_h)

    def stop(self) -> Dict[str, Any]:
        """Stop recording and return the session dict."""
        if not self._running:
            logger.warning("RichRecorder was not running.")
            return {}

        self._running = False
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._kb_listener:
            self._kb_listener.stop()

        self._flush_pending_text()

        session = {
            "session_id":   datetime.now().strftime("%Y-%m-%dT%H-%M-%S"),
            "recorded_at":  datetime.now().isoformat(timespec="seconds"),
            "platform":     platform.system(),
            "screen_width":  self._screen_w,
            "screen_height": self._screen_h,
            "packet_count":  len(self._packets),
            "packets":       self._packets,
        }
        logger.info("RichRecorder stopped — %d packets captured.", len(self._packets))
        return session

    def save(
        self,
        path: str,
        name: str = "unnamed_session",
        description: str = "",
    ) -> Path:
        """Stop (if running), inject metadata, and write JSON to disk."""
        if self._running:
            session = self.stop()
        else:
            session = {
                "session_id":    datetime.now().strftime("%Y-%m-%dT%H-%M-%S"),
                "recorded_at":   datetime.now().isoformat(timespec="seconds"),
                "platform":      platform.system(),
                "screen_width":  self._screen_w,
                "screen_height": self._screen_h,
                "packet_count":  len(self._packets),
                "packets":       self._packets,
            }

        session["name"]        = name
        session["description"] = description

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)

        size_mb = out.stat().st_size / 1_048_576
        logger.info(
            "Session saved → %s  (%d packets, %.1f MB)",
            out, len(self._packets), size_mb
        )
        return out

    @staticmethod
    def load(path: str) -> Dict[str, Any]:
        """Load a previously saved session from disk."""
        with open(path, "r", encoding="utf-8") as f:
            session = json.load(f)
        logger.info(
            "Session loaded: %s  (%d packets)",
            session.get("name", path), len(session.get("packets", []))
        )
        return session

    # ─────────────────────────────────────────────────────────── internals

    def _now(self) -> float:
        return time.monotonic() - self._start_time

    def _delta_and_idle(self) -> Tuple[float, float]:
        """
        Return (t_delta_s, idle_wait_s).
        idle_wait_s is non-zero only if the gap exceeds IDLE_THRESHOLD_S.
        """
        now   = self._now()
        delta = now - (self._last_action_t - self._start_time)
        idle  = delta if delta >= self.idle_threshold_s else 0.0
        self._last_action_t = time.monotonic()
        return round(delta, 3), round(idle, 3)

    def _grab_before(self) -> Optional["Image.Image"]:
        return _grab_screen() if self.capture_screenshots else None

    def _append_packet(
        self,
        action_type: str,
        extra: Dict[str, Any],
        before_img: Optional["Image.Image"],
    ) -> None:
        """Build and store one Rich Action Packet (thread-safe)."""
        t_offset             = self._now()
        t_delta, idle_wait   = self._delta_and_idle()

        packet = _build_packet(
            seq           = self._seq + 1,
            action_type   = action_type,
            t_offset_s    = t_offset,
            t_delta_s     = t_delta,
            idle_wait_s   = idle_wait,
            extra         = extra,
            before_img    = before_img,
            capture_after = self.capture_after_shot,
            jpeg_quality  = CLICK_JPEG_QUALITY if action_type == "click" else JPEG_QUALITY,
        )

        # Strip OCR fields if disabled
        if not self.run_ocr:
            packet["intent"] = {}

        # Strip semantic fields if disabled
        if not self.capture_semantics:
            packet["semantic"] = {}

        with self._lock:
            self._seq += 1
            packet["seq"] = self._seq
            self._packets.append(packet)

        logger.debug(
            "Packet #%d  %s  delta=%.2fs  idle=%.2fs",
            self._seq, action_type, t_delta, idle_wait
        )

    def _flush_pending_text(self) -> None:
        """Emit accumulated typed characters as one type() packet."""
        if not self._pending_text:
            return
        before = _grab_screen() if self.capture_screenshots else None
        self._append_packet(
            action_type = "type",
            extra       = {"text": self._pending_text},
            before_img  = before,
        )
        # Override t_offset to the time typing started
        if self._packets:
            self._packets[-1]["t_offset_s"] = round(self._pending_text_t, 3)
            self._packets[-1]["temporal"]["t_offset_s"] = round(self._pending_text_t, 3)
        self._pending_text = ""

    # ── pynput callbacks ──────────────────────────────────────────────────────

    def _on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        if not self._running or not pressed:
            return
        if x < 0 or y < 0 or x >= self._screen_w or y >= self._screen_h:
            logger.debug(
                "Ignoring click outside primary screen bounds: (%d, %d) not in %dx%d",
                x, y, self._screen_w, self._screen_h,
            )
            return
        self._flush_pending_text()
        before = self._grab_before()
        btn    = button.name if hasattr(button, "name") else str(button)
        self._append_packet(
            action_type = "click",
            extra       = {"x": x, "y": y, "button": btn, "clicks": 1},
            before_img  = before,
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._running:
            return
        if x < 0 or y < 0 or x >= self._screen_w or y >= self._screen_h:
            logger.debug(
                "Ignoring scroll outside primary screen bounds: (%d, %d) not in %dx%d",
                x, y, self._screen_w, self._screen_h,
            )
            return
        self._flush_pending_text()
        before = self._grab_before()
        direction = "up" if dy > 0 else ("down" if dy < 0 else ("right" if dx > 0 else "left"))
        amount    = abs(dy) if dy != 0 else abs(dx)
        self._append_packet(
            action_type = "scroll",
            extra       = {"x": x, "y": y, "direction": direction, "amount": max(amount, 1)},
            before_img  = before,
        )

    def _on_move(self, x: int, y: int) -> None:
        # Hover recording is disabled
        pass

    def _key_name(self, key: Any) -> str:
        if hasattr(key, "char") and key.char:
            return key.char
        if hasattr(key, "name"):
            return key.name.lower()
        return str(key).lower().replace("key.", "")

    def _key_char(self, key: Any) -> str:
        char = getattr(key, "char", None)
        return char if isinstance(char, str) else ""

    @staticmethod
    def _is_shift_modifier(name: str) -> bool:
        return name in {"shift", "shift_l", "shift_r"}

    _MODIFIERS = frozenset({
        "ctrl", "ctrl_l", "ctrl_r",
        "shift", "shift_l", "shift_r",
        "alt", "alt_l", "alt_r",
        "cmd", "win", "super_l", "super_r",
    })
    _SPECIAL_KEYS = frozenset({
        "enter", "return", "tab", "backspace", "delete", "del",
        "escape", "esc", "space", "up", "down", "left", "right",
        "home", "end", "pageup", "pagedown",
        *[f"f{i}" for i in range(1, 13)],
    })

    def _on_key_press(self, key: Any) -> None:
        if not self._running:
            return
        name = self._key_name(key)
        char = self._key_char(key)

        if name in self._MODIFIERS:
            self._held_modifiers.add(name)
            return

        # Ctrl/Alt/Win/Cmd chords are true hotkeys.
        # Shift alone with a printable character is regular typing.
        if self._held_modifiers:
            non_shift_mods = [m for m in self._held_modifiers if not self._is_shift_modifier(m)]
            if non_shift_mods:
                self._flush_pending_text()
                before = self._grab_before()
                chord  = sorted(self._held_modifiers) + [name]
                self._append_packet(
                    action_type = "hotkey",
                    extra       = {"keys": chord},
                    before_img  = before,
                )
                return
            if char:
                now = self._now()
                if not self._pending_text:
                    self._pending_text_t = now
                self._pending_text += char
                return

        # Special key alone
        if name in self._SPECIAL_KEYS:
            self._flush_pending_text()
            before = self._grab_before()
            self._append_packet(
                action_type = "hotkey",
                extra       = {"keys": [name]},
                before_img  = before,
            )
            return

        # Regular character — accumulate
        now = self._now()
        if not self._pending_text:
            self._pending_text_t = now
        self._pending_text += char or name

    def _on_key_release(self, key: Any) -> None:
        if not self._running:
            return
        self._held_modifiers.discard(self._key_name(key))
