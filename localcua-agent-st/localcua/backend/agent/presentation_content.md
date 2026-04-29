# Presentation Content: GUI Agent Automation Core Components

This document breaks down the three main pillars of the CUA (Computer Use Agent) backend for your presentation slides.

---

## Slide 1: action_parser.py — The "Translator"
**Role:** Converts raw Natural Language/VLM output into structured data the computer can understand.

*   **Regex-based Extraction**: Uses sophisticated regular expressions to pull function names (e.g., `click()`, `type()`) and arguments from AI-generated text.
*   **Coordinate Normalization**: 
    *   Translates the AI's internal coordinate system (e.g., 0 to 1000) into real-world screen pixels.
    *   Handles complex markup like `<bbox>` and `<point>` tags.
*   **Smart Resizing**: Includes logic to resize screenshots while preserving aspect ratios, ensuring the AI sees the UI clearly without distorting coordinates.
*   **Data Validation**: Ensures that every action has the necessary inputs (like `start_coords` or `text`) before passing it to the executor.

---

## Slide 2: executor.py — The "Hands"
**Role:** The physical interface that interacts with the Operating System using mouse and keyboard simulations.

*   **PyAutoGUI Core**: Utilizes the PyAutoGUI library to perform OS-level actions across different platforms (Windows, Linux, macOS).
*   **Safety First**:
    *   **Fail-Safe**: If things go wrong, the user can move the mouse to a corner to instantly kill the process.
    *   **Boundary Clamping**: Automatically prevents clicks from going "off-screen," which avoids OS crashes or stuck states.
*   **Diverse Action Support**:
    *   **Mouse**: Single/Double/Right clicks, hovering, and drag-and-drop.
    *   **Keyboard**: Supports hotkeys (e.g., `Ctrl+C`), special keys, and a robust "Clipboard Paste" method for typing complex Unicode text.
*   **Intelligent Scrolling**: Can target specific UI elements to scroll up/down/left/right by precise amounts.

---

## Slide 3: agent_loop.py — The "Brain/Orchestrator"
**Role:** Manages the autonomous "See → Think → Act" lifecycle of the agent.

*   **VLM Client**: Manages HTTP communication with the local AI inference server (GUI Agent), sending screenshots and receiving "Thoughts."
*   **Short-Term Memory (History)**: Internally tracks previous steps (Thoughts and Actions) and feeds them back to the AI so it doesn't get stuck in loops.
*   **The Execution Cycle**:
    1.  **Capture**: Takes a high-quality screenshot of the current state.
    2.  **Inference**: Sends screenshot + history to the model to decide the next move.
    3.  **Process**: Passes model text to the *Translator* (`action_parser`).
    4.  **Execute**: Commands the *Hands* (`executor`) to perform the action.
*   **Termination Logic**: Automatically stops when the model signals "Finished" or requests human assistance via "Call User."

---

## Slide 4: System Architecture Flow
**How they work together:**

1.  **agent_loop.py** triggers a screenshot.
2.  The screenshot is sent to the AI Model.
3.  The Model returns text: `"Thought: I need to open Chrome. Action: click(start_box=(200,300,200,300))"`
4.  **action_parser.py** turns that text into a Python Dictionary: `{"action_type": "click", "coords": [450, 600]}`
5.  **executor.py** moves the physical mouse and performs the click.
6.  The loop repeats.
