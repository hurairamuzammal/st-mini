"""
ui_tars_agent_wrapper.py
------------------------
Wrapper for a GUI Agent that enforces step-by-step execution and prevents
multi-step responses during macro replay.

This wrapper:
- Extracts current step from prompt
- Validates model output
- Prevents skipping/reordering
- Handles the finished() call
"""

# Purpose: Wraps a GUI agent to enforce strict one-step-at-a-time macro execution.

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class GUIAgentStepEnforcer:
    """
    Wrap a GUI Agent to enforce strict step-by-step replay.

    Usage:
        base_agent = YourGUIAgent(model="your-gui-agent-model")
        enforcer = GUIAgentStepEnforcer(base_agent)

        runner = RichScriptRunner(agent=enforcer)
        runner.run_file("sessions/my_task.json")
    """

    def __init__(
        self,
        base_agent: Any,
        max_steps: int = 100,
        temperature: float = 0.1,
        verbose: bool = True,
    ) -> None:
        """
        Parameters
        ----------
        base_agent : GUI Agent instance
        max_steps : Maximum steps to execute (safety limit)
        temperature : Sampling temperature (low = deterministic)
        verbose : Print execution logs
        """
        self.base_agent = base_agent
        self.max_steps = max_steps
        self.temperature = temperature
        self.verbose = verbose

        self.current_step = 1
        self.total_steps = 0
        self.completed = False

    def run(self, prompt: str, images: Optional[List[str]] = None) -> Any:
        """
        Run the agent with step enforcement.

        The agent executes the task step-by-step until finished() is called
        or max_steps is reached.
        """
        self._extract_total_steps(prompt)

        self.current_step = 1
        self.completed = False

        if self.verbose:
            print(f"\n{'=' * 60}")
            print(f"GUI AGENT REPLAY: {self.total_steps} steps to execute")
            print(f"{'=' * 60}\n")

        focused_prompt = self._focus_on_current_step(prompt)

        iteration = 0
        while not self.completed and iteration < self.max_steps:
            iteration += 1

            if self.verbose:
                print(f"\n>>> Executing Step {self.current_step}/{self.total_steps}")

            try:
                if images:
                    response = self.base_agent.run(focused_prompt, images=images)
                else:
                    response = self.base_agent.run(focused_prompt)
            except Exception as exc:
                logger.error("Agent error at step %s: %s", self.current_step, exc)
                return {"error": str(exc), "step": self.current_step}

            if self.verbose:
                print(f"Response: {response}")

            if self._is_finished(response):
                self.completed = True
                if self.verbose:
                    print(f"\nTask completed after {self.current_step} steps")
                return {"status": "completed", "steps_executed": self.current_step}

            self.current_step += 1

            if self.current_step > self.total_steps:
                if self.verbose:
                    print(f"\nAll {self.total_steps} steps executed")
                return {"status": "completed", "steps_executed": self.total_steps}

            focused_prompt = self._focus_on_current_step(prompt)

        logger.warning("Max iterations (%s) reached without completion", self.max_steps)
        return {"status": "timeout", "steps_executed": self.current_step - 1}

    def _extract_total_steps(self, prompt: str) -> None:
        """Extract total step count from prompt."""
        match = re.search(r"steps 1→(\d+)", prompt)
        if match:
            self.total_steps = int(match.group(1))
            return

        steps = re.findall(r"^\d+\.\s", prompt, re.MULTILINE)
        if steps:
            self.total_steps = len(steps)
            return

        self.total_steps = 10
        logger.warning("Could not extract step count, assuming 10")

    def _focus_on_current_step(self, prompt: str) -> str:
        """Modify prompt to emphasize the current step only."""
        steps_match = re.search(
            r"EXECUTE THESE STEPS IN ORDER.*?:\n(.*?)\n\n",
            prompt,
            re.DOTALL,
        )

        if not steps_match:
            return f"{prompt}\n\nNOW EXECUTE STEP {self.current_step} ONLY."

        all_steps = steps_match.group(1).strip()
        step_lines = all_steps.split("\n")

        current_step_text = ""
        for line in step_lines:
            if line.strip().startswith(f"{self.current_step}."):
                current_step_text = line.strip()
                break

        if not current_step_text:
            return f"{prompt}\n\nNOW EXECUTE STEP {self.current_step} ONLY."

        before_steps = prompt[: steps_match.start()]
        after_steps = prompt[steps_match.end() :]

        focused = f"""{before_steps}

ALL STEPS:
{all_steps}

-> CURRENT STEP TO EXECUTE NOW:
{current_step_text}

YOU MUST:
1. Look at the screenshot
2. Execute ONLY step {self.current_step}
3. Do NOT execute any other steps
4. After this action, stop and wait

{after_steps}"""

        return focused

    def _is_finished(self, response: Any) -> bool:
        """Check whether response indicates task completion."""
        if isinstance(response, dict):
            if response.get("status") == "completed":
                return True
            if response.get("action") == "finished":
                return True

        response_str = str(response).lower()
        return "finished()" in response_str or "task completed" in response_str


# Backward-compatible alias for existing imports.
UITARSStepEnforcer = GUIAgentStepEnforcer


if __name__ == "__main__":
    class MockGUIAgent:
        def run(self, prompt: str, images: Optional[List[str]] = None) -> dict:
            print(f"[Mock] Received prompt ({len(prompt)} chars)")
            if images:
                print(f"[Mock] With {len(images)} images")
            return {"action": "click", "x": 100, "y": 200}

    mock = MockGUIAgent()
    enforcer = GUIAgentStepEnforcer(mock, verbose=True)

    test_prompt = """REPLAY TASK: Test
APP: Notepad

EXECUTE THESE STEPS IN ORDER (one per turn):
1. click \"File\"
2. click \"New\"
3. type \"hello\"

RULES:
- Execute steps 1→3 in exact order
- Do ONE step per turn
- After step 3: call finished()

START WITH STEP 1 NOW."""

    result = enforcer.run(test_prompt)
    print(f"\nFinal result: {result}")
