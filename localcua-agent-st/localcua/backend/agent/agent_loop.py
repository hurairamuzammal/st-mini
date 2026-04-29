"""
agent_loop.py
-------------
Production agent loop for the GUI Agent VLM automation system.

Architecture
------------
    AgentLoop
        │
        ├── VLMClient          — sends screenshots to the GUI agent API
        │                        server and returns raw action text
        │
        ├── action_parser      — parses raw VLM text → structured action dicts
        │
        └── executor           — executes structured actions via PyAutoGUI

Quick start
-----------
    # 1. Start the VLM server in a separate terminal:
    #       python <your_api_server>.py
    #
    # 2. Run the agent:
    #       python agent_loop.py --task "Open Notepad and type Hello"

CLI flags
---------
    --task       TEXT   Natural-language task description (required)
    --url        URL    Base URL of the GUI agent API server
                        (default: http://localhost:5000)
    --max-steps  N      Maximum VLM → execute iterations before aborting
                        (default: 30)
    --delay      SECS   Seconds to pause between steps for the screen to settle
                        (default: 1.0)
    --no-failsafe       Disable PyAutoGUI corner fail-safe (use with caution)
    --log-level  LEVEL  Logging verbosity: DEBUG | INFO | WARNING (default: INFO)
"""

from __future__ import annotations

import argparse
import base64
import io
import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pyautogui
import requests
from PIL import Image

try:
    # Package import path (used when imported via scheduler runner).
    from .action_parser import parse_actions
    from .executor import configure as configure_executor
    from .executor import execute_actions
except ImportError:
    # Script execution path (used when running agent_loop.py directly).
    from action_parser import parse_actions
    from executor import configure as configure_executor
    from executor import execute_actions

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

VALID_RUN_MODES = {"command_bar", "task_schedule"}
PROMPT_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", flags=re.UNICODE)
ACTION_CALL_START_PATTERN = re.compile(r"\b[A-Za-z_]\w*\s*\(")


def _normalise_run_mode(mode: str) -> str:
    cleaned = (mode or "").strip().lower()
    if cleaned in VALID_RUN_MODES:
        return cleaned
    return "command_bar"


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(PROMPT_TOKEN_PATTERN.findall(text))


def _truncate_text_to_token_limit(text: str, max_tokens: int) -> Tuple[str, int, bool]:
    """
    Approximate token-safe truncation using line boundaries first.
    Returns (truncated_text, approx_tokens, was_truncated).
    """
    if not text:
        return "", 0, False

    if max_tokens <= 0:
        return "", 0, True

    total_tokens = _estimate_tokens(text)
    if total_tokens <= max_tokens:
        return text, total_tokens, False

    kept_lines: List[str] = []
    used_tokens = 0
    for line in text.splitlines():
        line_tokens = _estimate_tokens(line) + 1
        if used_tokens + line_tokens > max_tokens:
            break
        kept_lines.append(line)
        used_tokens += line_tokens

    if kept_lines:
        truncated = "\n".join(kept_lines).rstrip()
    else:
        chunks = re.findall(r"\S+\s*", text)
        out_chunks: List[str] = []
        used_tokens = 0
        for chunk in chunks:
            chunk_tokens = _estimate_tokens(chunk)
            if used_tokens + chunk_tokens > max_tokens:
                break
            out_chunks.append(chunk)
            used_tokens += chunk_tokens
        truncated = "".join(out_chunks).strip()

    note = (
        "\n\n[Task prompt was trimmed to stay within the configured token budget. "
        "Proceed using the available instructions.]"
    )
    note_tokens = _estimate_tokens(note)
    current_tokens = _estimate_tokens(truncated)
    if current_tokens + note_tokens <= max_tokens:
        truncated = f"{truncated}{note}"
        current_tokens += note_tokens

    return truncated, current_tokens, True


def _extract_first_balanced_action_call(text: str) -> Optional[str]:
    """
    Extract the first function-like call (e.g. click(...)) with balanced
    parentheses from free-form model output.
    """
    if not text:
        return None

    match = ACTION_CALL_START_PATTERN.search(text)
    if not match:
        return None

    open_paren = text.find("(", match.start())
    if open_paren < 0:
        return None

    depth = 0
    for idx in range(open_paren, len(text)):
        char = text[idx]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[match.start():idx + 1].strip()

    return None


def _build_system_prompt(run_mode: str) -> str:
    """Build system prompt with explicit two-mode behavior contract."""
    mode = _normalise_run_mode(run_mode)
    active_mode_text = (
        "TASK_SCHEDULE" if mode == "task_schedule" else "COMMAND_BAR"
    )
    schedule_mode_rules = ""
    if mode == "task_schedule":
        schedule_mode_rules = (
            "- TASK_SCHEDULE is strict replay mode: this is replay, not planning.\n"
            "- Execute only the next unfinished recorded step.\n"
            "- Execute the recorded steps exactly once and in order.\n"
            "- Do not invent, optimize, skip, reorder, or merge steps.\n"
            "- Do not perform any action that is not required by the current step.\n"
            "- Use exactly one GUI action call per response.\n"
            "- The Action line must contain exactly one function call and nothing else.\n"
            "- Never output multiple actions, lists, or future-step plans.\n"
            "- For type steps, enter the exact text shown in the step (preserve case, spaces, and symbols).\n"
            "- For hotkey steps, press the exact key chord shown in the step once.\n"
            "- If the target app/element is not visible, perform only the minimum corrective navigation to continue the current step.\n"
            "- If still blocked after reasonable attempts, emit call_user() instead of guessing.\n"
            "- After the final scheduled step, emit finished() immediately and stop.\n"
        )

    return (
        "You are a GUI automation agent powered by a GUI agent model.\n"
        "There are exactly two modes:\n"
        "1) TASK_SCHEDULE mode: mimic the recorded user workflow and follow "
        "the provided steps in order to complete the same task.\n"
        "2) COMMAND_BAR mode: execute the live user instruction passed from "
        "the command bar.\n"
        f"Current mode: {active_mode_text}.\n\n"
        "Rules:\n"
        "- Always use the latest screenshot to ground actions visually.\n"
        "- If UI layout changed, find equivalent elements and continue.\n"
        f"{schedule_mode_rules}"
        "- Output exactly:\n"
        "  Thought: <one-sentence reasoning>\n"
        "  Action: <one GUI action call>\n"
        "- The Action line must be a single action call with no extra prose.\n"
        "- Emit finished() when the task is complete.\n"
        "- Emit call_user() only when user input is required."
    )


def _build_task_instruction(task: str, run_mode: str) -> str:
    mode = _normalise_run_mode(run_mode)
    mode_label = "TASK_SCHEDULE" if mode == "task_schedule" else "COMMAND_BAR"

    if mode == "task_schedule":
        task_intro = (
            "This task came from the scheduler. Replay the recorded macro literally. "
            "Execute only the next unfinished numbered step per turn, exactly once and in order. "
            "Do not optimize, combine, or reinterpret steps. "
            "Treat the provided numbered steps as the source of truth and emit finished() "
            "immediately after the final step."
        )
    else:
        task_intro = (
            "This task came from the command bar. Interpret the user goal and "
            "execute it directly in the current UI."
        )

    return (
        f"Mode: {mode_label}\n"
        f"{task_intro}\n\n"
        "Task:\n"
        f"{task}"
    )


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """
    All tuneable parameters for the agent in one place.

    Fields
    ------
    task            Natural-language task description sent to the VLM.
    api_url         Base URL of the GUI agent API server.
    max_steps       Hard cap on the number of VLM → execute iterations.
    step_delay      Seconds to wait after each action batch for the UI to settle.
    action_delay    Passed to executor.configure() as pyautogui.PAUSE.
    failsafe        Passed to executor.configure() as pyautogui.FAILSAFE.
    model_v15       Enable legacy coordinate normalisation in action_parser.
    request_timeout HTTP timeout in seconds for each VLM call.
    history         Whether to include previous (thought, action) pairs in the
                    prompt so the VLM has short-term memory across steps.
    max_history     Maximum number of past steps to keep in the prompt.
    """
    task:            str
    api_url:         str   = "http://127.0.0.1:8080"
    max_steps:       int   = 30
    step_delay:      float = 1.0
    action_delay:    float = 0.05
    failsafe:        bool  = True
    model_v15:       bool  = True
    request_timeout: int   = 300
    history:         bool  = True
    max_history:     int   = 5
    max_image_dim:   int   = 1080  # Max dimension for VLM image (speeds up inference)
    run_mode:        str   = "command_bar"
    prompt_token_limit: int = 8000


# ---------------------------------------------------------------------------
# VLM Client
# ---------------------------------------------------------------------------

class VLMClient:
    """
    Thin HTTP client for the GUI agent inference server.

    Expected server endpoint
    ------------------------
    POST /v1/chat/completions
    Content-Type: application/json

    Request body (OpenAI vision format):
        {
            "model": "<model-name>",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "prompt"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
                    ]
                }
            ]
        }

    Response body:
        {
            "choices": [{"message": {"content": "<raw VLM output text>"}}]
        }
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 60,
        prompt_token_limit: int = 8000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self.prompt_token_limit = max(256, int(prompt_token_limit))
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_image(img: Image.Image) -> str:
        """Convert a PIL Image to a base64-encoded PNG string."""
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def infer(
        self,
        screenshot: Image.Image,
        task: str,
        run_mode: str = "command_bar",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """
        Send a screenshot + prompt to the VLM server and return the raw text.

        Parameters
        ----------
        screenshot  PIL Image of the current screen state.
        task        Natural-language task description.
        history     Optional list of {"thought": ..., "action": ...} dicts
                    representing previous steps.
        """
        image_b64 = self._encode_image(screenshot)

        mode = _normalise_run_mode(run_mode)
        system_prompt = _build_system_prompt(mode)
        task_instruction = _build_task_instruction(task, mode)

        # Reserve some budget for history and protocol overhead.
        system_tokens = _estimate_tokens(system_prompt)
        reserve_tokens = 512
        task_budget = max(
            256,
            self.prompt_token_limit - system_tokens - reserve_tokens,
        )
        bounded_task, bounded_task_tokens, task_was_trimmed = _truncate_text_to_token_limit(
            task_instruction,
            task_budget,
        )
        if task_was_trimmed:
            logger.warning(
                "Task prompt was trimmed in %s mode to fit ~%d token limit "
                "(system=%d, task<=%d, used=%d)",
                mode,
                self.prompt_token_limit,
                system_tokens,
                task_budget,
                bounded_task_tokens,
            )

        # System message + task instruction.
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": bounded_task,
            }
        ]

        # Inject memory using native chat roles
        if history:
            if mode == "task_schedule":
                executed_actions = [
                    str(step.get("action", "")).strip()
                    for step in history
                    if str(step.get("action", "")).strip()
                ]
                if executed_actions:
                    recent_actions = executed_actions[-8:]
                    executed_lines = "\n".join(
                        f"{idx}. {action}" for idx, action in enumerate(recent_actions, 1)
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "Already executed actions in this run:\n"
                            f"{executed_lines}\n\n"
                            "Continue TASK_SCHEDULE replay from the next unfinished recorded step.\n"
                            "Do not repeat completed steps.\n"
                            "Return exactly one GUI action call."
                        ),
                    })
            else:
                for step in history:
                    messages.append({
                        "role": "assistant",
                        "content": f"Thought: {step['thought']}\nAction: {step['action']}"
                    })
                    messages.append({
                        "role": "user",
                        "content": "Action executed. Here is the new screenshot. What is the next Action?"
                    })

        # Attach the latest screenshot to the final user message
        if messages[-1]["role"] == "user":
            # If the last message was a user message, upgrade its content to a list
            # containing the existing text + the new image
            text_val = messages[-1]["content"]
            messages[-1]["content"] = [
                {"type": "text", "text": text_val},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
            ]
        else:
            # Fallback (should not be reached based on above logic)
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
                ]
            })

        payload = {
            "model": "ui-tars",
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.0,
        }

        url = self.base_url.rstrip("/")
        if not url.endswith("/v1/chat/completions"):
            url = f"{url}/v1/chat/completions"

        try:
            resp = self._session.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            err_msg = resp.text
            raise ConnectionError(
                f"VLM server returned HTTP {resp.status_code}: {err_msg}"
            ) from exc
        except requests.ConnectionError as exc:
            raise ConnectionError(
                f"Cannot reach VLM server at {url}. "
                "Is the server running?"
            ) from exc

        data = resp.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as exc:
            raise ValueError(f"VLM server returned unexpected JSON format: {data}") from exc


# ---------------------------------------------------------------------------
# Agent Loop
# ---------------------------------------------------------------------------

class AgentLoop:
    """
    Main control loop: screenshot → VLM → parse → execute, repeat.

    Usage
    -----
        config = AgentConfig(task="Open Notepad and type Hello")
        agent  = AgentLoop(config)
        agent.run()
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.config.run_mode = _normalise_run_mode(self.config.run_mode)
        self.client = VLMClient(
            config.api_url,
            timeout=config.request_timeout,
            prompt_token_limit=config.prompt_token_limit,
        )

        # Short-term memory: list of {"thought": ..., "action": ...}
        self._history: List[Dict[str, str]] = []

        # Cumulative results for post-run inspection
        self.step_results: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _take_screenshot(self) -> Tuple[Image.Image, Tuple[int, int]]:
        """Capture the screen, optionally resize for VLM, return (Image, (w, h))."""
        img = pyautogui.screenshot()
        raw_w, raw_h = img.width, img.height

        max_dim = self.config.max_image_dim
        if max_dim > 0 and max(raw_w, raw_h) > max_dim:
            ratio = max_dim / max(raw_w, raw_h)
            new_w, new_h = int(raw_w * ratio), int(raw_h * ratio)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
        logger.debug("screenshot: raw=%dx%d, sent=%dx%d", raw_w, raw_h, img.width, img.height)
        return img, (img.width, img.height)

    def _get_screen_context(self) -> Dict[str, int]:
        """Return current screen dimensions for action_parser."""
        w, h = pyautogui.size()
        return {"width": w, "height": h}

    @staticmethod
    def _extract_thought(raw: str) -> str:
        """Pull the Thought: line out of the VLM response."""
        for line in raw.splitlines():
            if line.strip().lower().startswith("thought:"):
                return line.split(":", 1)[-1].strip()
        return ""

    @staticmethod
    def _extract_action_text(raw: str) -> str:
        """Pull the Action: line(s) out of the VLM response."""
        lines: List[str] = []
        capture = False
        for line in raw.splitlines():
            if line.strip().lower().startswith("action:"):
                capture = True
                rest = line.split(":", 1)[-1].strip()
                if rest:
                    lines.append(rest)
            elif capture:
                lines.append(line)
        return "\n".join(lines).strip()

    def _update_history(self, thought: str, action_text: str) -> None:
        """Append to short-term memory, honouring max_history."""
        if not self.config.history:
            return
        self._history.append({"thought": thought, "action": action_text})
        if len(self._history) > self.config.max_history:
            self._history.pop(0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> List[Dict[str, Any]]:
        """
        Execute the agent loop until the task is finished, the model calls
        call_user(), or max_steps is reached.

        Returns
        -------
        List of step result dicts:
            {
                "step":         int,
                "thought":      str,
                "action_text":  str,
                "exec_results": List[dict],   # from executor.execute_actions()
                "terminal":     bool,
                "call_user":    bool,
            }
        """
        cfg        = self.config
        screen_ctx = self._get_screen_context()

        logger.info("=" * 60)
        logger.info("Agent starting — task: %r", cfg.task)
        logger.info("VLM server  : %s", cfg.api_url)
        logger.info("Max steps   : %d", cfg.max_steps)
        logger.info("Run mode    : %s", cfg.run_mode)
        logger.info("Prompt cap  : ~%d tokens", cfg.prompt_token_limit)
        logger.info("Screen      : %dx%d", screen_ctx["width"], screen_ctx["height"])
        logger.info("=" * 60)

        configure_executor(action_delay=cfg.action_delay, failsafe=cfg.failsafe)

        for step in range(1, cfg.max_steps + 1):
            logger.info(
                "── Step %d/%d ──────────────────────────────────", step, cfg.max_steps
            )

            # 1. Screenshot
            screenshot, vlm_size = self._take_screenshot()

            # 2. Call VLM
            try:
                raw_response = self.client.infer(
                    screenshot,
                    task=cfg.task,
                    run_mode=cfg.run_mode,
                    history=self._history if cfg.history else None,
                )
            except (ConnectionError, requests.RequestException) as exc:
                logger.error("VLM call failed at step %d: %s", step, exc)
                self.step_results.append({
                    "step":      step,
                    "error":     str(exc),
                    "terminal":  True,
                    "call_user": False,
                })
                break

            logger.info("VLM response:\n%s", raw_response)

            # 3. Extract thought + action text
            thought     = self._extract_thought(raw_response)
            action_text = self._extract_action_text(raw_response)

            logger.info("Thought : %s", thought or "(none)")
            logger.info("Action  : %s", action_text or "(none)")

            if not action_text:
                logger.warning(
                    "Step %d: no Action: found in VLM response — skipping", step
                )
                self.step_results.append({
                    "step":         step,
                    "thought":      thought,
                    "action_text":  "",
                    "exec_results": [],
                    "terminal":     False,
                    "call_user":    False,
                })
                continue

            # 4. Parse actions
            # Since llama.cpp predicts coordinates relative to the image size we sent,
            # we pass the vlm_size as factors. action_parser maps them back to the 
            # physical screen width/height!
            actions = parse_actions(
                action_text,
                factors=(vlm_size[0], vlm_size[1]),
                screen_context=screen_ctx,
                model_v15=False,
            )

            if cfg.run_mode == "task_schedule":
                valid_actions = [
                    action
                    for action in actions
                    if not action.get("error") and action.get("action_type")
                ]

                if not valid_actions:
                    recovered_call = _extract_first_balanced_action_call(action_text)
                    if recovered_call and recovered_call != action_text.strip():
                        recovered_actions = parse_actions(
                            recovered_call,
                            factors=(vlm_size[0], vlm_size[1]),
                            screen_context=screen_ctx,
                            model_v15=False,
                        )
                        recovered_valid = [
                            action
                            for action in recovered_actions
                            if not action.get("error") and action.get("action_type")
                        ]
                        if recovered_valid:
                            logger.warning(
                                "TASK_SCHEDULE guard: recovered first action call from noisy model output: %s",
                                recovered_call,
                            )
                            actions = recovered_actions
                            valid_actions = recovered_valid

                if len(valid_actions) > 1:
                    logger.warning(
                        "TASK_SCHEDULE guard: model emitted %d actions in one response; "
                        "executing only the first (%s).",
                        len(valid_actions),
                        valid_actions[0].get("action_type"),
                    )
                    actions = [valid_actions[0]]
                elif len(actions) > 1 and valid_actions:
                    logger.warning(
                        "TASK_SCHEDULE guard: parsed %d entries (including invalid); "
                        "executing first valid action (%s).",
                        len(actions),
                        valid_actions[0].get("action_type"),
                    )
                    actions = [valid_actions[0]]

            # 5. Execute actions
            exec_results = execute_actions(actions, stop_on_finish=True)

            # 6. Check terminal signals
            terminal  = any(r.get("terminal")  for r in exec_results)
            call_user = any(r.get("call_user") for r in exec_results)

            step_record: Dict[str, Any] = {
                "step":         step,
                "thought":      thought,
                "action_text":  action_text,
                "exec_results": exec_results,
                "terminal":     terminal,
                "call_user":    call_user,
            }
            self.step_results.append(step_record)
            self._update_history(thought, action_text)

            if call_user:
                logger.info("Model requested human input — pausing agent loop.")
                break

            if terminal:
                logger.info("Task finished signal received at step %d.", step)
                break

            # 7. Wait for UI to settle before next step
            if cfg.step_delay > 0:
                time.sleep(cfg.step_delay)

        else:
            logger.warning(
                "Max steps (%d) reached without a finish signal.", cfg.max_steps
            )

        logger.info(
            "Agent loop complete — %d step(s) executed.", len(self.step_results)
        )
        return self.step_results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent_loop",
        description="GUI Agent VLM automation agent",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--task", required=True,
        help="Natural-language description of what the agent should do.",
    )
    p.add_argument(
        "--url", default="http://127.0.0.1:8080",
        help="Base URL of the GUI agent VLM server.",
    )
    p.add_argument(
        "--max-steps", type=int, default=30,
        help="Maximum number of VLM → execute iterations.",
    )
    p.add_argument(
        "--delay", type=float, default=1.0,
        help="Seconds to pause between steps for the screen to settle.",
    )
    p.add_argument(
        "--timeout", type=int, default=300,
        help="HTTP request timeout in seconds for VLM inference.",
    )
    p.add_argument(
        "--max-dim", type=int, default=1024,
        help="Resize screenshot so max dimension equals this value (dramatically speeds up inference).",
    )
    p.add_argument(
        "--mode",
        default="command_bar",
        choices=["command_bar", "task_schedule"],
        help="Execution mode: command-bar intent or scheduled macro replay.",
    )
    p.add_argument(
        "--prompt-token-limit",
        type=int,
        default=8000,
        help="Approximate prompt token budget enforced before each model call.",
    )
    p.add_argument(
        "--no-failsafe", action="store_true",
        help="Disable PyAutoGUI corner fail-safe (use with caution).",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return p


def main() -> None:
    parser = _build_arg_parser()
    args   = parser.parse_args()

    _setup_logging(args.log_level)

    config = AgentConfig(
        task            = args.task,
        api_url         = args.url,
        max_steps       = args.max_steps,
        step_delay      = args.delay,
        failsafe        = not args.no_failsafe,
        request_timeout = args.timeout,
        max_image_dim   = args.max_dim,
        run_mode        = args.mode,
        prompt_token_limit = args.prompt_token_limit,
    )

    agent   = AgentLoop(config)
    results = agent.run()

    successes = sum(
        all(r.get("success", True) for r in step.get("exec_results", []))
        for step in results
    )
    logger.info(
        "Summary: %d/%d steps had all actions succeed.",
        successes, len(results),
    )


if __name__ == "__main__":
    main()