# this is main scheduler

"""
executor.py
-----------
Executes parsed GUI Agent actions using PyAutoGUI.

Compatible with the output of action_parser.parse_actions(), which returns:
    [
        {
            "action_type":   str,           # lower-cased GUI action function name
            "action_inputs": dict,          # resolved keys including start_coords /
                                            # end_coords for pointer actions
            "error":         bool,
        },
        ...
    ]

Supported action types
----------------------
    click           single left-click at start_coords
    left_click      alias for click
    double_click    double left-click at start_coords
    right_click     right-click at start_coords
    middle_click    middle-click at start_coords
    hover           move mouse to start_coords (no click)
    drag            click-drag from start_coords to end_coords
    swipe           alias for drag
    type            type a string (action_inputs["text"])
    key             press a key or hotkey (action_inputs["key"])
    press           alias for key
    hotkey          press a multi-key chord (action_inputs["key"])
    scroll          scroll at start_coords by action_inputs["direction"] /
                    action_inputs["amount"]
    screenshot      take a screenshot; returns PIL Image
    wait            sleep for action_inputs.get("time", 1.0) seconds
    finished        terminal action — model signals task complete (official name)
    finish          alias for finished
    call_user       model signals it needs human input; sets call_user=True in result

Usage
-----
    from action_parser import parse_actions
    from executor import configure, execute_actions

    import pyautogui

    # Call configure() once at agent startup before any execute_* calls.
    # Do NOT rely on import-time side effects -- this module is safe to
    # import without mutating pyautogui globals.
    configure()

    screen_w, screen_h = pyautogui.size()

    actions = parse_actions(
        model_output,
        screen_context={"width": screen_w, "height": screen_h},
        model_v15=True,
    )
    results = execute_actions(actions)
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import pyautogui

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global operator configuration
# ---------------------------------------------------------------------------

# Seconds between every pyautogui call — reduces mis-clicks on slow machines
DEFAULT_ACTION_DELAY: float = 0.05

# Mouse movement duration (seconds).  0.0 = instant; 0.2 feels natural.
DEFAULT_MOVE_DURATION: float = 0.2

# Pixels the mouse is allowed to be off-screen before the call is rejected.
# PyAutoGUI clamps by default but we surface a warning for large violations.
OFFSCREEN_TOLERANCE: int = 10

# Guard so configure() is idempotent -- safe to call more than once.
_configured: bool = False


def configure(
    action_delay: float = DEFAULT_ACTION_DELAY,
    failsafe: bool = True,
    force: bool = False,
) -> None:
    """
    Apply pyautogui globals for this executor.

    Must be called once at agent startup -- NOT at import time -- so that
    importing this module does not silently mutate pyautogui state for any
    other code sharing the same process (tests, screenshot utilities, etc.).

    Parameters
    ----------
    action_delay   Seconds pyautogui waits after every call (pyautogui.PAUSE).
                   Matches DEFAULT_ACTION_DELAY by default.
    failsafe       If True, moving the mouse to a screen corner aborts the
                   agent (pyautogui.FAILSAFE). Recommended for production.
    force          Re-apply even if configure() has already been called.
    """
    global _configured
    if _configured and not force:
        return
    pyautogui.FAILSAFE = failsafe
    pyautogui.PAUSE    = action_delay
    _configured = True
    logger.debug(
        "executor configured: FAILSAFE=%s PAUSE=%.3fs",
        failsafe, action_delay,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_screen_size() -> Tuple[int, int]:
    """Return (width, height) of the primary monitor."""
    return pyautogui.size()


def _validate_coords(x: int, y: int) -> Tuple[int, int]:
    """
    Clamp (x, y) to the screen bounds and log a warning if clamping was needed.
    Returns the (possibly clamped) coordinates.
    """
    sw, sh = _get_screen_size()
    cx = max(0, min(x, sw - 1))
    cy = max(0, min(y, sh - 1))
    if cx != x or cy != y:
        logger.warning(
            "_validate_coords: (%d, %d) out of bounds %dx%d — clamped to (%d, %d)",
            x, y, sw, sh, cx, cy,
        )
    return cx, cy


def _coords_from_inputs(action_inputs: Dict[str, Any], key: str = "start_coords") -> Optional[Tuple[int, int]]:
    """
    Extract and validate a coordinate pair from action_inputs.
    Returns None and logs a warning if the key is missing or malformed.
    """
    raw = action_inputs.get(key)
    if raw is None:
        logger.warning("_coords_from_inputs: key %r not found in action_inputs=%s", key, action_inputs)
        return None
    try:
        x, y = int(raw[0]), int(raw[1])
    except (TypeError, IndexError, ValueError) as exc:
        logger.warning("_coords_from_inputs: bad value for %r=%r: %s", key, raw, exc)
        return None
    return _validate_coords(x, y)


def _parse_key_string(key_str: str) -> List[str]:
    """
    Convert a GUI action key string to a list of individual key names for
    pyautogui.hotkey().

    The parser accepts several formats:
        "ctrl+c"       → ["ctrl", "c"]
        "Return"       → ["return"]          (X11 keysym → pyautogui name)
        "Key.enter"    → ["enter"]           (pynput-style)
        "ctrl shift t" → ["ctrl", "shift", "t"]
    """
    # Pynput-style "Key.xxx"
    key_str = key_str.strip()
    if key_str.lower().startswith("key."):
        key_str = key_str[4:]

    # X11 / Tk keysym aliases → pyautogui names
    _ALIASES: Dict[str, str] = {
        "return":    "enter",
        "escape":    "esc",
        "delete":    "del",
        "backspace": "backspace",
        "prior":     "pageup",
        "next":      "pagedown",
        "super_l":   "win",
        "super_r":   "win",
        "alt_l":     "alt",
        "alt_r":     "alt",
        "control_l": "ctrl",
        "control_r": "ctrl",
        "shift_l":   "shift",
        "shift_r":   "shift",
        "space":     "space",
        "tab":       "tab",
    }

    # Split on "+" or whitespace
    parts = re.split(r"[+\s]+", key_str.lower())
    parts = [p.strip() for p in parts if p.strip()]
    return [_ALIASES.get(p, p) for p in parts]


# ---------------------------------------------------------------------------
# Action handlers — one function per action type
# ---------------------------------------------------------------------------

def _do_click(action_inputs: Dict[str, Any], button: str = "left", clicks: int = 1) -> Dict[str, Any]:
    coords = _coords_from_inputs(action_inputs, "start_coords")
    if coords is None:
        return {"success": False, "reason": "missing start_coords"}
    x, y = coords
    click_interval = 0.1 if clicks > 1 else 0.0
    pyautogui.click(x, y, button=button, clicks=clicks, interval=click_interval, duration=DEFAULT_MOVE_DURATION)
    logger.debug("click button=%s clicks=%d at (%d, %d)", button, clicks, x, y)
    return {"success": True, "x": x, "y": y, "button": button, "clicks": clicks}


def _do_hover(action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    coords = _coords_from_inputs(action_inputs, "start_coords")
    if coords is None:
        return {"success": False, "reason": "missing start_coords"}
    x, y = coords
    pyautogui.moveTo(x, y, duration=DEFAULT_MOVE_DURATION)
    logger.debug("hover at (%d, %d)", x, y)
    return {"success": True, "x": x, "y": y}


def _do_drag(action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    start = _coords_from_inputs(action_inputs, "start_coords")
    end   = _coords_from_inputs(action_inputs, "end_coords")
    if start is None:
        return {"success": False, "reason": "missing start_coords"}
    if end is None:
        return {"success": False, "reason": "missing end_coords"}
    sx, sy = start
    ex, ey = end
    pyautogui.moveTo(sx, sy, duration=DEFAULT_MOVE_DURATION)
    pyautogui.dragTo(ex, ey, duration=max(DEFAULT_MOVE_DURATION, 0.3), button="left")
    logger.debug("drag from (%d, %d) to (%d, %d)", sx, sy, ex, ey)
    return {"success": True, "start": list(start), "end": list(end)}


def _do_type(action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    text = action_inputs.get("text", "")
    if not isinstance(text, str):
        text = str(text)
    if not text:
        logger.warning("_do_type: empty text payload")
        return {"success": False, "reason": "empty text"}

    # The model often emits type(content="...", start_box="...") to indicate
    # which field to type into. Click it first so focus is correct before typing.
    coords = _coords_from_inputs(action_inputs, "start_coords")
    if coords is not None:
        cx, cy = coords
        pyautogui.click(cx, cy, duration=DEFAULT_MOVE_DURATION)
        time.sleep(0.1)  # brief pause for the field to gain focus
        logger.debug("type: clicked target field at (%d, %d) before typing", cx, cy)

    # pyautogui.typewrite does not handle unicode well; use pyperclip + hotkey
    # as a robust fallback for non-ASCII characters.
    try:
        import pyperclip  # optional dependency
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        logger.debug("type (clipboard paste) %r", text[:40])
    except ImportError:
        pyautogui.typewrite(text, interval=0.02)
        logger.debug("type (typewrite) %r", text[:40])
    return {"success": True, "text": text, "clicked_first": coords is not None}


def _do_key(action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Press a single key or chord."""
    raw = action_inputs.get("key", action_inputs.get("content", ""))
    if not raw:
        logger.warning("_do_key: no key specified in action_inputs=%s", action_inputs)
        return {"success": False, "reason": "no key specified"}
    keys = _parse_key_string(str(raw))
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)
    logger.debug("key %s", keys)
    return {"success": True, "keys": keys}


def _do_scroll(action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scroll at start_coords.

    action_inputs recognised keys:
        direction   "up" | "down" | "left" | "right"  (default "down")
        amount      number of scroll units              (default 3)
    """
    coords = _coords_from_inputs(action_inputs, "start_coords")
    if coords is None:
        # The model always provides start_box for scroll; missing coords
        # means something went wrong upstream. Warn explicitly rather than
        # silently scrolling at the current mouse position (wrong element risk).
        current = pyautogui.position()
        logger.warning(
            "_do_scroll: start_coords missing — falling back to current mouse "
            "position %s. This may scroll the wrong element.",
            current,
        )
        coords = (current.x, current.y)

    x, y = coords
    direction = str(action_inputs.get("direction", "down")).lower()
    try:
        amount = int(action_inputs.get("amount", 3))
    except (TypeError, ValueError):
        amount = 3

    if direction in ("up",):
        pyautogui.scroll(amount, x=x, y=y)
    elif direction in ("down",):
        pyautogui.scroll(-amount, x=x, y=y)
    elif direction in ("left",):
        pyautogui.hscroll(-amount, x=x, y=y)
    elif direction in ("right",):
        pyautogui.hscroll(amount, x=x, y=y)
    else:
        logger.warning("_do_scroll: unknown direction %r, defaulting to down", direction)
        pyautogui.scroll(-amount, x=x, y=y)

    logger.debug("scroll dir=%s amount=%d at (%d, %d)", direction, amount, x, y)
    return {"success": True, "direction": direction, "amount": amount, "x": x, "y": y}


def _do_screenshot(_action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Capture the full screen. Returns the PIL Image in the result dict."""
    img = pyautogui.screenshot()
    logger.debug("screenshot taken (%dx%d)", img.width, img.height)
    return {"success": True, "image": img, "width": img.width, "height": img.height}


def _do_wait(action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        seconds = float(action_inputs.get("time", action_inputs.get("duration", 1.0)))
    except (TypeError, ValueError):
        seconds = 1.0
    seconds = max(0.0, min(seconds, 30.0))  # cap at 30 s for safety
    time.sleep(seconds)
    logger.debug("wait %.2fs", seconds)
    return {"success": True, "seconds": seconds}


def _do_finish(_action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Terminal action — the model signals the task is complete."""
    logger.debug("finished action received")
    return {"success": True, "terminal": True}


def _do_call_user(_action_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    call_user() is a distinct official action — the model signals it is
    stuck and needs human input to proceed.  We surface this as a clean signal
    (terminal=True, call_user=True) so the caller can prompt the user rather
    than treating it as an unknown/error action.
    """
    logger.info("call_user action received — model is requesting human assistance")
    return {"success": True, "terminal": True, "call_user": True}


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_HANDLERS: Dict[str, Any] = {
    # click variants
    "click":         lambda i: _do_click(i, button="left",   clicks=1),
    "left_click":    lambda i: _do_click(i, button="left",   clicks=1),
    "left_single_click": lambda i: _do_click(i, button="left", clicks=1),
    "left_single":   lambda i: _do_click(i, button="left",   clicks=1),
    "double_click":  lambda i: _do_click(i, button="left",   clicks=2),
    "left_double_click": lambda i: _do_click(i, button="left", clicks=2),
    "left_double":   lambda i: _do_click(i, button="left",   clicks=2),
    "right_click":   lambda i: _do_click(i, button="right",  clicks=1),
    "right_single_click": lambda i: _do_click(i, button="right", clicks=1),
    "right_single":  lambda i: _do_click(i, button="right",  clicks=1),
    "right_double_click": lambda i: _do_click(i, button="right", clicks=2),
    "right_double":  lambda i: _do_click(i, button="right",  clicks=2),
    "middle_click":  lambda i: _do_click(i, button="middle", clicks=1),
    # mouse movement
    "hover":         _do_hover,
    "move":          _do_hover,
    # drag / swipe
    "drag":          _do_drag,
    "swipe":         _do_drag,
    # keyboard
    "type":          _do_type,
    "input":         _do_type,
    "key":           _do_key,
    "press":         _do_key,
    "hotkey":        _do_key,
    "key_press":     _do_key,
    # scroll
    "scroll":        _do_scroll,
    # system
    "screenshot":    _do_screenshot,
    "wait":          _do_wait,
    "sleep":         _do_wait,
    # terminal — official action name is "finished()"; "finish" kept as alias
    "finished":      _do_finish,
    "finish":        _do_finish,
    "done":          _do_finish,
    "stop":          _do_finish,
    # call_user() is a distinct official action — model needs human input
    "call_user":     _do_call_user,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_action(action: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a single parsed action dict and return a result dict.

    The result always contains:
        success   bool   – whether the action completed without error
        action    str    – the action_type that was executed
        error_msg str    – present only on failure
    """
    if action.get("error"):
        logger.warning("execute_action: skipping unparseable action: %s", action)
        return {"success": False, "action": "", "error_msg": "unparseable action from parser"}

    action_type   = action.get("action_type", "").lower().strip()
    action_inputs = action.get("action_inputs", {})

    if not action_type:
        return {"success": False, "action": action_type, "error_msg": "empty action_type"}

    handler = _HANDLERS.get(action_type)
    if handler is None:
        logger.warning("execute_action: unknown action_type %r", action_type)
        return {
            "success":   False,
            "action":    action_type,
            "error_msg": f"unsupported action type: {action_type!r}",
        }

    try:
        result = handler(action_inputs)
        result["action"] = action_type
        return result
    except pyautogui.FailSafeException:
        logger.error("PyAutoGUI fail-safe triggered (mouse in corner)")
        raise
    except Exception as exc:
        logger.exception("execute_action: exception in handler for %r", action_type)
        return {
            "success":   False,
            "action":    action_type,
            "error_msg": str(exc),
        }


def execute_actions(
    actions: List[Dict[str, Any]],
    stop_on_error: bool = False,
    stop_on_finish: bool = True,
    inter_action_delay: float = DEFAULT_ACTION_DELAY,
) -> List[Dict[str, Any]]:
    """
    Execute a list of parsed actions in sequence.

    Parameters
    ----------
    actions             Output of action_parser.parse_actions()
    stop_on_error       Abort the sequence on the first failed action
    stop_on_finish      Stop when a "finish" / terminal action is encountered
    inter_action_delay  Extra sleep between actions (seconds)

    Returns
    -------
    List of result dicts, one per executed action.
    """
    results: List[Dict[str, Any]] = []

    for i, action in enumerate(actions):
        logger.info(
            "execute_actions [%d/%d]: action_type=%r",
            i + 1, len(actions), action.get("action_type"),
        )

        result = execute_action(action)
        results.append(result)

        if not result.get("success") and stop_on_error:
            logger.error(
                "execute_actions: stopping at action %d due to error: %s",
                i + 1, result.get("error_msg"),
            )
            break

        if result.get("terminal") and stop_on_finish:
            logger.info("execute_actions: finish action received, stopping sequence")
            break

        if inter_action_delay > 0 and i < len(actions) - 1:
            time.sleep(inter_action_delay)

    return results
