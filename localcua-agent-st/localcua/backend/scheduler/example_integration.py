"""
example_integration.py
----------------------
Complete example showing how to integrate the optimized components
with a GUI Agent via Ollama.

This demonstrates:
1. Loading the optimized RichScriptRunner
2. Wrapping the agent with step enforcement
3. Running a recorded macro
"""

# Purpose: Demonstrates how to wire scheduler replay components to a GUI agent implementation.

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scheduler.rich_script_runner import RichScriptRunner
from scheduler.ui_tars_agent_wrapper import GUIAgentStepEnforcer


class OllamaGUIAgent:
    """
    Simple wrapper for an Ollama-hosted GUI Agent model.

    Install:
        ollama pull <your-gui-agent-model>

    Or if using a custom model:
        ollama create <your-gui-agent-model> -f Modelfile
    """

    def __init__(
        self,
        model: str = "ui-tars:7b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.1,
    ) -> None:
        try:
            import ollama  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("ollama-python not installed. Run: pip install ollama") from exc

        self.client = ollama.Client(host=base_url)
        self.model = model
        self.temperature = temperature

    def run(self, prompt: str, images: Optional[list] = None) -> str:
        """
        Send prompt to Ollama and return response text.

        Some setups may require model-specific image support for vision input.
        """
        messages = [{"role": "user", "content": prompt}]

        if images:
            print("[Warning] Image support depends on model/runtime capabilities")
            print("[Info] Running text-only for compatibility")

        try:
            response = self.client.chat(
                model=self.model,
                messages=messages,
                options={"temperature": self.temperature, "num_predict": 512},
            )
            return response["message"]["content"]
        except Exception as exc:
            print(f"[Error] Ollama request failed: {exc}")
            return f"ERROR: {exc}"


class CustomGUIAgent:
    """Wrapper for a custom GUI Agent API endpoint."""

    def __init__(self, api_url: str = "http://localhost:8000/v1/chat") -> None:
        import requests

        self.api_url = api_url
        self.session = requests.Session()

    def run(self, prompt: str, images: Optional[list] = None) -> str:
        payload = {
            "prompt": prompt,
            "max_tokens": 512,
            "temperature": 0.1,
        }

        if images:
            payload["images"] = images

        try:
            response = self.session.post(self.api_url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("response", "")
        except Exception as exc:
            return f"ERROR: {exc}"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run GUI Agent macro replay")
    parser.add_argument("--session", required=True, help="Path to recorded session JSON")
    parser.add_argument("--model", default="ui-tars:7b", help="Ollama model name")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama server URL")
    parser.add_argument(
        "--inject-visuals",
        action="store_true",
        help="Include screenshot crops (requires vision-capable model)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print detailed execution logs")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without executing")

    args = parser.parse_args()

    if args.dry_run:
        agent = None
        print("[Dry run mode - no agent execution]")
    else:
        print(f"Initializing Ollama GUI Agent: {args.model}")
        base_agent = OllamaGUIAgent(
            model=args.model,
            base_url=args.ollama_url,
            temperature=0.1,
        )
        agent = GUIAgentStepEnforcer(
            base_agent=base_agent,
            max_steps=100,
            verbose=args.verbose,
        )

    runner = RichScriptRunner(
        agent=agent,
        include_coords=True,
        skip_hover=True,
        inject_visuals=args.inject_visuals,
    )

    print(f"\nLoading session: {args.session}")
    result = runner.run_file(args.session)

    if args.dry_run:
        print("\n[Dry run completed - prompt shown above]")
    else:
        print(f"\nExecution result: {result}")


if __name__ == "__main__":
    main()


def quick_test() -> None:
    """Quick test without command-line args."""
    session_file = "sessions/test_macro.json"

    print("=" * 70)
    print("DRY RUN - Showing optimized prompt")
    print("=" * 70)
    runner = RichScriptRunner(agent=None)
    runner.run_file(session_file)

    # Real execution sample:
    # base_agent = OllamaGUIAgent(model="your-gui-agent-model")
    # enforcer = GUIAgentStepEnforcer(base_agent, verbose=True)
    # runner = RichScriptRunner(agent=enforcer)
    # result = runner.run_file(session_file)
    # print(f"Final result: {result}")


# quick_test()
