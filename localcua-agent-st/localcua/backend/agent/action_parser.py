import re
import math
import logging
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

# -----------------------------
# Helper functions
# -----------------------------
def round_by_factor(num: float, factor: int) -> int:
    return round(num / factor) * factor

def floor_by_factor(num: float, factor: int) -> int:
    return math.floor(num / factor) * factor

def ceil_by_factor(num: float, factor: int) -> int:
    return math.ceil(num / factor) * factor

def smart_resize_v15(
    height: int,
    width: int,
    max_ratio: float = 10,
    factor: int = 32,
    min_pixels: int = 256 * 256,
    max_pixels: int = 1344 * 1344,
) -> Optional[Tuple[int, int]]:
    if min(height, width) == 0:
        logger.warning("smart_resize_v15: zero dimension received (h=%d, w=%d)", height, width)
        return None

    if max(height, width) / min(height, width) > max_ratio:
        logger.warning("smart_resize_v15: aspect ratio too large (h=%d, w=%d)", height, width)
        return None

    w_bar = max(factor, round_by_factor(width, factor))
    h_bar = max(factor, round_by_factor(height, factor))

    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(height / beta, factor)
        w_bar = floor_by_factor(width / beta, factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(height * beta, factor)
        w_bar = ceil_by_factor(width * beta, factor)

    return w_bar, h_bar


def normalize_box(
    nums: List[float],
    factors: Tuple[int, int],
    smart_resize: Optional[Tuple[int, int]] = None,
) -> List[float]:
    """
    Normalize coordinates to [0, 1] range.

    factors / smart_resize must be (width, height) so that
    index 0 (x) divides by width and index 1 (y) divides by height.
    """
    normalized = []
    for i, num in enumerate(nums):
        axis = i % 2  # 0 = x-axis, 1 = y-axis
        divisor = smart_resize[axis] if smart_resize else factors[axis]
        normalized.append(num / divisor)

    # Promote a bare (x, y) point to a degenerate box (x1, y1, x2, y2)
    if len(normalized) == 2:
        normalized = normalized + normalized

    return normalized


# -----------------------------
# Action string parser
# -----------------------------

# FIX 3: replaced variable-length lookbehind (unsupported in Python re) # pragma: no mutate
# with two explicit substitutions that are unambiguous and correct.
_POINT_ALIASES = {
    "start_point": "start_box",
    "end_point":   "end_box",
}

# FIX 2: matches `key=(...)` or `key='(...)'` or `key=value` without splitting on commas # pragma: no mutate
# that live inside parentheses.
_KWARG_RE = re.compile(r'(\w+)=([\'"]?[(\[][^)\]]*[)\]][\'"]?|[^,]+)')


def parse_action_string(action_str: str) -> Optional[Dict[str, Any]]:
    """Parse a single GUI action string into {function, args}."""
    try:
        # Strip model box sentinel tokens
        action_str = action_str.replace("<|box_start|>", "").replace("<|box_end|>", "")

        # FIX 3: normalise point= aliases without a variable-length lookbehind
        for old, new in _POINT_ALIASES.items():
            action_str = action_str.replace(f"{old}=", f"{new}=")
        # Bare `point=` (no start_/end_ prefix) → treat as start_box
        action_str = re.sub(r'\bpoint=', 'start_box=', action_str)

        match = re.match(r'^(\w+)\((.*)\)$', action_str.strip(), re.DOTALL)
        if not match:
            return None

        func_name, args_str = match.groups()
        kwargs: Dict[str, str] = {}

        if args_str.strip():
            # FIX 2: use paren-aware regex so (x,y,x,y) tuples are not split
            for key, value in _KWARG_RE.findall(args_str):
                value = value.strip().strip("'\"")

                # Normalise <bbox>…</bbox> markup
                if "<bbox>" in value:
                    value = re.sub(r"<\/?bbox>", "", value).replace(" ", ",")
                    value = f"({value})"

                # Normalise <point>…</point> markup
                if "<point>" in value:
                    value = re.sub(r"<\/?point>", "", value).replace(" ", ",")
                    value = f"({value})"

                kwargs[key] = value

        return {"function": func_name, "args": kwargs}

    except Exception as exc:
        logger.warning("parse_action_string failed: %s | input: %r", exc, action_str)
        return None


# -----------------------------
# Full action parser
# -----------------------------

def parse_actions(
    text: str,
    factors: Tuple[int, int] = (1000, 1000),
    screen_context: Optional[Dict[str, int]] = None,
    scale_factor: float = 1.0,
    model_v15: bool = False,
) -> List[Dict[str, Any]]:
    """
    Parse one or more GUI action strings from *text*. # pragma: no mutate

    Returns a list of dicts, each with:
        action_type   – lower-cased function name (empty string on parse failure)
        action_inputs – extracted and coordinate-resolved parameters # pragma: no mutate
        error         – True only when parsing failed for this entry
    """
    text = text.strip()
    action_str = text.split("Action:")[-1] if "Action:" in text else text
    all_actions = [a.strip() for a in action_str.split("\n\n") if a.strip()]

    # FIX: guard against None return from smart_resize_v15
    smart_resize: Optional[Tuple[int, int]] = None
    if model_v15 and screen_context:
        smart_resize = smart_resize_v15(screen_context["height"], screen_context["width"])
        if smart_resize is None:
            logger.warning(
                "smart_resize_v15 returned None for screen_context=%s; "
                "falling back to raw factors",
                screen_context,
            )

    results: List[Dict[str, Any]] = []

    for raw in all_actions:
        parsed = parse_action_string(raw)

        # FIX: include an explicit error flag so callers can distinguish
        # a failed parse from a valid action with no inputs.
        if parsed is None:
            results.append({"action_type": "", "action_inputs": {}, "error": True})
            continue

        action_type = parsed["function"].lower()
        params = parsed["args"]
        action_inputs: Dict[str, Any] = {}

        # FIX 1: collect start_box and end_box coords separately so that
        # drag/swipe end coordinates are computed from end_box, not start_box.
        start_normalized: Optional[List[float]] = None
        end_normalized:   Optional[List[float]] = None

        for key, val in params.items():
            val = val.strip()

            if "box" in key or "point" in key:
                nums = [float(n) for n in re.sub(r"[()\[\]]", "", val).split(",") if n]
                norm = normalize_box(nums, factors, smart_resize)
                action_inputs[key] = norm

                if "start" in key or key == "start_box":
                    start_normalized = norm
                elif "end" in key or key == "end_box":
                    end_normalized = norm

            # FIX 4: accept the content/text/value key names that the model
            # actually emits for type/input actions, not just a hardcoded "text".
            elif action_type in ("type", "input") and key in ("content", "text", "value"):
                action_inputs["text"] = val

            else:
                action_inputs[key] = val

        # Resolve pixel coordinates from normalised boxes
        if screen_context and start_normalized is not None:
            if len(start_normalized) >= 4:
                x1, y1, x2, y2 = start_normalized[:4]
                cx = ((x1 + x2) / 2) * screen_context["width"]
                cy = ((y1 + y2) / 2) * screen_context["height"]
                action_inputs["start_coords"] = [
                    round(cx * scale_factor),
                    round(cy * scale_factor),
                ]
            else:
                logger.warning("parse_actions: invalid start_normalized length %s", start_normalized)

        # FIX 1: for drag/swipe use end_box (not start_box) for end_coords
        if screen_context and action_type in ("drag", "swipe"):
            src = end_normalized if end_normalized is not None else start_normalized
            if src is not None:
                if len(src) >= 4:
                    ex1, ey1, ex2, ey2 = src[:4]
                    ecx = ((ex1 + ex2) / 2) * screen_context["width"]
                    ecy = ((ey1 + ey2) / 2) * screen_context["height"]
                    action_inputs["end_coords"] = [
                        round(ecx * scale_factor),
                        round(ecy * scale_factor),
                    ]
                else:
                    logger.warning("parse_actions: invalid src for end_coords %s", src)

        results.append({
            "action_type":   action_type,
            "action_inputs": action_inputs,
            "error":         False,
        })

    return results
