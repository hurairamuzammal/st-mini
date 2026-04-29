"""
rich_script_runner.py 
---------------------
Converts Rich Action Packets into ultra-concise prompts that stay within
the 8096 token context window while maximizing visual grounding for the GUI Agent.

Key optimizations:
- Removes verbose explanations
- Focuses on visual targets (OCR text, control names)
- Minimal coordinates (only as fallback)
- One-line step format
- Truncates long sessions intelligently
"""

# Purpose: Converts recorded packets into compact, step-by-step prompts for GUI-agent replay.

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Token budget for GUI Agent 7B
MAX_PROMPT_TOKENS = 6000  # Leave 2096 for model output
CHARS_PER_TOKEN = 4       # Rough estimate


def _estimate_tokens(text: str) -> int:
    """Rough token estimation."""
    return len(text) // CHARS_PER_TOKEN


def _normalise_hotkey_token(token: Any) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("key."):
        raw = raw[4:]
    if len(raw) == 1:
        code = ord(raw)
        if 1 <= code <= 26:
            raw = chr(code + 96)
    
    key = raw.lower()
    aliases = {
        "ctrl_l": "ctrl", "ctrl_r": "ctrl", "control": "ctrl",
        "alt_l": "alt", "alt_r": "alt",
        "shift_l": "shift", "shift_r": "shift",
        "super_l": "win", "super_r": "win", "cmd": "win",
        "return": "enter", "escape": "esc", "delete": "del",
    }
    return aliases.get(key, key)


def _format_hotkey(keys: List[Any]) -> str:
    formatted: List[str] = []
    seen: set[str] = set()
    
    for token in keys:
        norm = _normalise_hotkey_token(token)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        formatted.append(norm.upper())
    
    return "+".join(formatted) if formatted else "???"


def _get_visual_target(pkt: Dict[str, Any]) -> str:
    """Extract the most useful visual identifier for targeting."""
    intent = pkt.get("intent", {})
    semantic = pkt.get("semantic", {})
    
    # Priority 1: Text under cursor (most precise)
    text_under = (intent.get("text_under_cursor") or "").strip()
    if text_under and len(text_under) < 30:
        return f'"{text_under}"'
    
    # Priority 2: Control name (accessibility label)
    ctrl_name = (semantic.get("ctrl_name") or "").strip()
    if ctrl_name and len(ctrl_name) < 30:
        return f'"{ctrl_name}"'
    
    # Priority 3: First word(s) of nearby text
    text_nearby = (intent.get("text_nearby") or "").strip()
    if text_nearby:
        words = text_nearby.split()[:3]
        short = " ".join(words)
        if len(short) < 30:
            return f'near "{short}"'
    
    return ""


def _packet_to_compact_step(pkt: Dict[str, Any], step_num: int) -> str:
    """Convert packet to ultra-compact single-line step."""
    atype = pkt.get("action_type", "unknown")
    visual_target = _get_visual_target(pkt)
    
    # Build compact action description
    if atype == "click":
        x, y = pkt.get("x", "?"), pkt.get("y", "?")
        btn = pkt.get("button", "left")
        action = f"{btn}-click"
        if visual_target:
            return f"{step_num}. {action} {visual_target}"
        else:
            return f"{step_num}. {action} at ({x},{y})"
    
    elif atype == "type":
        text = pkt.get("text", "")
        # Truncate very long text
        if len(text) > 50:
            text = text[:47] + "..."
        return f'{step_num}. type "{text}"'
    
    elif atype == "hotkey":
        keys = pkt.get("keys", [])
        combo = _format_hotkey(keys)
        return f"{step_num}. press {combo}"
    
    elif atype == "scroll":
        direction = pkt.get("direction", "down")
        amount = pkt.get("amount", 3)
        return f"{step_num}. scroll {direction} {amount}x"
    
    elif atype == "double_click":
        x, y = pkt.get("x", "?"), pkt.get("y", "?")
        if visual_target:
            return f"{step_num}. double-click {visual_target}"
        else:
            return f"{step_num}. double-click at ({x},{y})"
    
    else:
        return f"{step_num}. {atype}"


def _get_app_context(packets: List[Dict[str, Any]]) -> str:
    """Extract main application context from first few packets."""
    for pkt in packets[:3]:
        semantic = pkt.get("semantic", {})
        proc_name = semantic.get("process_name", "").strip()
        win_title = semantic.get("window_title", "").strip()
        
        if proc_name and proc_name != "Program Manager":
            return proc_name
        if win_title and "notepad" in win_title.lower():
            return "Notepad"
        if win_title and "chrome" in win_title.lower():
            return "Chrome"
    
    return "Windows"


def build_rich_task_prompt(
    session: Dict[str, Any],
    include_coords: bool = True,
    skip_hover: bool = True,
) -> str:
    """
    Build ultra-compact prompt for the GUI Agent.
    
    Format:
        REPLAY TASK: [name]
        APP: [app_name]
        
        EXECUTE THESE STEPS IN ORDER:
        1. action description
        2. action description
        ...
        
        RULES:
        - Do ONE step per turn
        - Look at screenshot to find each target
        - After all steps: call finished()
    """
    name = session.get("name", "recorded task")
    packets = session.get("packets", [])
    
    # Filter out hover
    if skip_hover:
        packets = [p for p in packets if p.get("action_type") != "hover"]
    
    if not packets:
        return "REPLAY TASK: (no steps recorded)"
    
    # Get application context
    app_context = _get_app_context(packets)
    
    # Build compact steps
    steps = []
    for i, pkt in enumerate(packets, 1):
        step = _packet_to_compact_step(pkt, i)
        steps.append(step)
    
    steps_text = "\n".join(steps)
    
    # Ultra-compact prompt
    prompt = f"""REPLAY TASK: {name}
APP: {app_context}

EXECUTE THESE STEPS IN ORDER (one per turn):
{steps_text}

RULES:
- Execute steps 1→{len(packets)} in exact order
- Do ONE step per turn, then STOP
- Look at screenshot to visually locate each element
- Text in quotes = visual target to find
- Coordinates = fallback if text not visible
- After step {len(packets)}: call finished()

START WITH STEP 1 NOW."""
    
    # Check token budget
    estimated_tokens = _estimate_tokens(prompt)
    if estimated_tokens > MAX_PROMPT_TOKENS:
        logger.warning(
            f"Prompt exceeds token budget: {estimated_tokens} > {MAX_PROMPT_TOKENS}. "
            f"Truncating to first {int(MAX_PROMPT_TOKENS * 0.8)} tokens worth of steps."
        )
        # Keep only steps that fit
        target_chars = int(MAX_PROMPT_TOKENS * 0.8 * CHARS_PER_TOKEN)
        kept_steps = []
        running_length = 200  # Account for header/footer
        
        for step in steps:
            if running_length + len(step) > target_chars:
                break
            kept_steps.append(step)
            running_length += len(step) + 1
        
        steps_text = "\n".join(kept_steps)
        prompt = f"""REPLAY TASK: {name}
APP: {app_context}

EXECUTE THESE STEPS IN ORDER (one per turn):
{steps_text}

[Note: {len(packets) - len(kept_steps)} steps truncated to fit token limit]

RULES:
- Execute steps 1→{len(kept_steps)} in exact order
- Do ONE step per turn, then STOP
- Look at screenshot to find each element
- After step {len(kept_steps)}: call finished()

START WITH STEP 1 NOW."""
    
    return prompt.strip()


class RichScriptRunner:
    """
    Loads Rich Action Packet sessions and runs them via a GUI Agent.
    Optimized for 7B model with strict token budgets.
    """
    
    def __init__(
        self,
        agent: Optional[Any] = None,
        include_coords: bool = True,
        skip_hover: bool = True,
        inject_visuals: bool = False,
    ) -> None:
        self.agent = agent
        self.include_coords = include_coords
        self.skip_hover = skip_hover
        self.inject_visuals = inject_visuals
    
    def run_session(self, session: Dict[str, Any]) -> Optional[Any]:
        """Build compact prompt and submit to agent."""
        packets = session.get("packets", [])
        if not packets:
            logger.warning("RichScriptRunner: session has no packets.")
            return None
        
        prompt = build_rich_task_prompt(
            session,
            include_coords=self.include_coords,
            skip_hover=self.skip_hover,
        )
        
        logger.info(
            "RichScriptRunner: '%s' → %d packets → %d chars (~%d tokens)",
            session.get("name", "unnamed"),
            len(packets),
            len(prompt),
            _estimate_tokens(prompt),
        )
        
        # Collect reference images (only crops, not full screenshots)
        images: List[str] = []
        if self.inject_visuals:
            for pkt in packets:
                vis = pkt.get("visual", {})
                # Use crop (smaller) instead of full screenshot
                crop_b64 = vis.get("crop_b64")
                if crop_b64:
                    images.append(crop_b64)
                    # Limit to first 10 images to save tokens
                    if len(images) >= 10:
                        break
        
        if self.agent is None:
            # Dry-run
            print("\n" + "=" * 70)
            print("COMPACT TASK PROMPT FOR GUI AGENT 7B")
            print("=" * 70)
            print(prompt)
            if images:
                print(f"\n[{len(images)} reference crops attached]")
            print("=" * 70)
            print(f"Estimated tokens: {_estimate_tokens(prompt)}")
            print("=" * 70 + "\n")
            return prompt
        
        # Run with agent
        if self.inject_visuals and images:
            try:
                return self.agent.run(prompt, images=images)
            except TypeError:
                logger.warning(
                    "agent.run() does not accept 'images' parameter. "
                    "Running text-only."
                )
                return self.agent.run(prompt)
        else:
            return self.agent.run(prompt)
    
    def run_file(self, path: str) -> Optional[Any]:
        """Load session from disk and run it."""
        with open(path, "r", encoding="utf-8") as f:
            session = json.load(f)
        return self.run_session(session)