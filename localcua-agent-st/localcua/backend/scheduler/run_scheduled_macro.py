# Purpose: Runs a recorded scheduler macro session through the backend GUI agent loop.
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agent.agent_loop import AgentConfig, AgentLoop, _setup_logging
from scheduler.rich_script_runner import build_rich_task_prompt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_scheduled_macro",
        description="Execute a recorded macro session through the agent loop.",
    )
    parser.add_argument("--session", required=True, help="Path to recorded macro session JSON.")
    parser.add_argument("--task-text", default=None, help="Path to pre-generated text task prompt.")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="GUI Agent API base URL.")
    parser.add_argument("--max-steps", type=int, default=40, help="Maximum agent steps.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between steps in seconds.")
    parser.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds.")
    parser.add_argument("--max-dim", type=int, default=1024, help="Screenshot max dimension.")
    parser.add_argument(
        "--mode",
        choices=["task_schedule", "command_bar"],
        default="task_schedule",
        help="Agent execution mode. Scheduled jobs should run in task_schedule mode.",
    )
    parser.add_argument(
        "--prompt-token-limit",
        type=int,
        default=6000,
        help="Approximate token cap for the combined model prompt.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    _setup_logging(args.log_level)
    logger = logging.getLogger("run_scheduled_macro")

    session_path = Path(args.session)
    if not session_path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    with session_path.open("r", encoding="utf-8") as f:
        session = json.load(f)

    rebuilt_prompt = build_rich_task_prompt(
        session,
        include_coords=True,
        skip_hover=True,
    )

    # Keep optional metadata header from pre-generated prompt files, but always
    # use the latest runtime prompt template for the actionable content.
    header = ""
    if args.task_text:
        task_text_path = Path(args.task_text)
        if task_text_path.exists():
            raw = task_text_path.read_text(encoding="utf-8")
            header_lines = [ln for ln in raw.splitlines() if ln.strip().startswith("#")]
            if header_lines:
                header = "\n".join(header_lines).strip()

    task_prompt = f"{header}\n\n{rebuilt_prompt}".strip() if header else rebuilt_prompt

    config = AgentConfig(
        task=task_prompt,
        api_url=args.url,
        max_steps=args.max_steps,
        step_delay=args.delay,
        failsafe=True,
        request_timeout=args.timeout,
        max_image_dim=args.max_dim,
        run_mode=args.mode,
        prompt_token_limit=args.prompt_token_limit,
        history=True,
        max_history=2,
    )

    logger.info("Executing scheduled macro from: %s", session_path)
    AgentLoop(config).run()


if __name__ == "__main__":
    main()
