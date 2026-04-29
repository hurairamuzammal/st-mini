from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import subprocess
import os
import sys

from scheduler.scheduler_api import MacroSchedulerManager
from tests.window_pinner import WindowPinner

def check_dependencies():
    """Ensure required packages are available."""
    print("\n[System] Checking dependencies...")
    try:
        import fastapi
        import uvicorn
        import pydantic
        import apscheduler
        import PIL
        import pynput
        import pygetwindow
        import requests
        import pyautogui
        import pyperclip
        import win32gui
        print("[System] Dependencies OK.")
    except ImportError as e:
        print(f"[System] Missing dependency: {e.name}. Installing...")
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            "fastapi",
            "uvicorn",
            "pydantic",
            "apscheduler",
            "pillow",
            "pynput",
            "pygetwindow",
            "requests",
            "pyautogui",
            "pyperclip",
            "pywin32",
        ])

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str


class RecordStopRequest(BaseModel):
    name: str
    description: str = ""
    run_at: str


class RescheduleRequest(BaseModel):
    run_at: str


class WindowPinRequest(BaseModel):
    title: str = "LocalCUA"


class RecordStopDialogRequest(BaseModel):
    name: str = "Untitled Macro"
    description: str = ""

active_process = None
scheduler_manager: MacroSchedulerManager | None = None
window_pinner: WindowPinner | None = None


@app.on_event("startup")
async def on_startup() -> None:
    global scheduler_manager, window_pinner
    base_dir = Path(__file__).resolve().parent
    scheduler_manager = MacroSchedulerManager(base_dir=base_dir)
    window_pinner = WindowPinner(title="LocalCUA")
    print("[System] Macro scheduler initialized.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global scheduler_manager, window_pinner
    if scheduler_manager is not None:
        scheduler_manager.shutdown()
        scheduler_manager = None
    if window_pinner is not None:
        window_pinner.unpin()
        window_pinner = None

@app.post("/chat")
async def chat(request: ChatRequest):
    global active_process
    
    print("\n" + "="*50)
    print(f"DEBUG: Received message from Flutter!")
    print(f"PROMPT: {request.message}")
    print("="*50 + "\n")
    
    # Terminate existing process if running
    if active_process and active_process.poll() is None:
        try:
            print("[System] Stopping previous agent process...")
            pid = active_process.pid
            if os.name == 'nt':
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], 
                             capture_output=True, check=False)
            else:
                active_process.terminate()
            active_process.wait(timeout=2)
            active_process = None
        except Exception as e:
            print(f"[Error] Failed to terminate existing process: {e}")
    
    # Resolve path to agent_loop.py (just to confirm it exists for logging)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    agent_script = os.path.join(base_dir, "agent", "agent_loop.py")
    if not os.path.exists(agent_script):
         # Fallback search as before...
         parent_dir = os.path.dirname(base_dir)
         alt_script = os.path.join(parent_dir, "agent", "agent_loop.py")
         if os.path.exists(alt_script):
             agent_script = alt_script

    print(f"[System] Generating steps for task: \"{request.message}\"")
    
    # In a real scenario, you'd call a VLM or LLM here.
    # For now, we return a simulated response without starting the agent process.
    response_text = f"Analyzing task: {request.message}"
    steps = [
        f"Confirming current screen state for: {request.message}.",
        "Identifying UI elements to interact with.",
        "Executing visual automation sequence."
    ]
    
    return {
        "response": response_text,
        "steps": steps
    }

@app.post("/execute")
async def execute(request: ChatRequest):
    global active_process
    
    print("\n" + "="*50)
    print(f"DEBUG: EXECUTION TRIGGERED!")
    print(f"TASK: {request.message}")
    print("="*50 + "\n")

    # Terminate existing process if running (safety check)
    if active_process and active_process.poll() is None:
        try:
            pid = active_process.pid
            if os.name == 'nt':
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], 
                             capture_output=True, check=False)
            else:
                active_process.terminate()
            active_process.wait(timeout=3)
        except Exception:
            pass

    # Resolve path to agent_loop.py
    base_dir = os.path.dirname(os.path.abspath(__file__))
    agent_script = os.path.join(base_dir, "agent", "agent_loop.py")
    if not os.path.exists(agent_script):
        parent_dir = os.path.dirname(base_dir)
        alt_script = os.path.join(parent_dir, "agent", "agent_loop.py")
        if os.path.exists(alt_script):
            agent_script = alt_script
        else:
            cwd_script = os.path.join(os.getcwd(), "agent", "agent_loop.py")
            if os.path.exists(cwd_script):
                agent_script = cwd_script

    agent_dir = os.path.dirname(agent_script)
    
    if not os.path.exists(agent_script):
        err = f"Agent script not found at {agent_script}"
        print(f"[Error] {err}")
        return {"status": "error", "message": err}

    print(f"[System] Launching agent loop for: \"{request.message}\"")
    
    try:
        active_process = subprocess.Popen(
            [
                sys.executable,
                agent_script,
                "--task",
                request.message,
                "--mode",
                "command_bar",
                "--prompt-token-limit",
                "8000",
            ],
            cwd=agent_dir
        )
        return {"status": "started", "pid": active_process.pid}
    except Exception as e:
        print(f"[Error] Execution failed: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/stop")
async def stop():
    global active_process
    print("\n[System] Stop request received from Flutter.")
    
    if active_process and active_process.poll() is None:
        try:
            pid = active_process.pid
            print(f"[System] Terminating agent process hierarchy (PID: {pid})...")
            
            if os.name == 'nt':
                # Forcefully kill the process tree on Windows
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], 
                             capture_output=True, check=False)
            else:
                # Use SIGTERM on Unix/Linux
                active_process.terminate()
            
            # Brief wait to ensure cleanup
            try:
                active_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name != 'nt':
                    active_process.kill()
            
            active_process = None
            print("[System] Agent halted successfully.")
            return {"status": "stopped"}
        except Exception as e:
            print(f"[Error] Failed to stop agent: {e}")
            return {"status": f"error: {str(e)}"}
            
    print("[System] No active agent loop to stop.")
    return {"status": "not_running"}


@app.get("/scheduler/record/status")
async def scheduler_record_status():
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    return scheduler_manager.recording_status()


@app.post("/scheduler/record/start")
async def scheduler_record_start():
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    try:
        return scheduler_manager.start_recording()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start recording: {exc}") from exc


@app.post("/scheduler/record/cancel")
async def scheduler_record_cancel():
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    try:
        return scheduler_manager.cancel_recording()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to cancel recording: {exc}") from exc


@app.post("/scheduler/record/stop")
async def scheduler_record_stop(request: RecordStopRequest):
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    try:
        task = scheduler_manager.stop_and_schedule(
            name=request.name,
            description=request.description,
            run_at=request.run_at,
        )
        return {"status": "scheduled", "task": task}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to stop and schedule recording: {exc}") from exc


@app.post("/scheduler/record/stop-with-dialog")
async def scheduler_record_stop_with_dialog(request: RecordStopDialogRequest):
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    try:
        task = scheduler_manager.stop_and_schedule_with_dialog(
            name=request.name,
            description=request.description,
        )
        if task.get("status") == "cancelled":
            return {"status": "cancelled"}
        return {"status": "scheduled", "task": task}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to stop recording with dialog: {exc}") from exc


@app.get("/scheduler/tasks")
async def scheduler_list_tasks():
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    return {"tasks": scheduler_manager.list_schedules()}


@app.patch("/scheduler/tasks/{schedule_id}")
async def scheduler_reschedule_task(schedule_id: int, request: RescheduleRequest):
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    try:
        task = scheduler_manager.reschedule(schedule_id, request.run_at)
        return {"status": "rescheduled", "task": task}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to reschedule task: {exc}") from exc


@app.delete("/scheduler/tasks/{schedule_id}")
async def scheduler_delete_task(schedule_id: int):
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    try:
        return scheduler_manager.delete_schedule(schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete task: {exc}") from exc


@app.post("/scheduler/tasks/{schedule_id}/execute-now")
async def scheduler_execute_now(schedule_id: int):
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    try:
        task = scheduler_manager.execute_now(schedule_id)
        return {"status": "started", "task": task}
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute task now: {exc}") from exc


@app.post("/scheduler/tasks/{schedule_id}/stop")
async def scheduler_stop_running_task(schedule_id: int):
    if scheduler_manager is None:
        raise HTTPException(status_code=503, detail="Scheduler manager not initialized")
    try:
        task = scheduler_manager.stop_schedule(schedule_id)
        return {"status": "stopped", "task": task}
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to stop running task: {exc}") from exc


@app.post("/window/pin")
async def pin_window(request: WindowPinRequest):
    global window_pinner
    if window_pinner is None:
        window_pinner = WindowPinner(title=request.title)
    else:
        window_pinner.title = request.title

    result = window_pinner.pin()
    if result.status == "error":
        raise HTTPException(status_code=500, detail=result.detail)
    if result.status == "unsupported":
        raise HTTPException(status_code=400, detail=result.detail)
    return {"status": result.status, "detail": result.detail}


@app.post("/window/unpin")
async def unpin_window():
    if window_pinner is None:
        return {"status": "not_pinned", "detail": "Window pinner is not initialized"}

    result = window_pinner.unpin()
    if result.status == "error":
        raise HTTPException(status_code=500, detail=result.detail)
    if result.status == "unsupported":
        raise HTTPException(status_code=400, detail=result.detail)
    return {"status": result.status, "detail": result.detail}


@app.get("/window/pin/status")
async def pin_status():
    if window_pinner is None:
        return {"available": False, "is_pinned": False, "title": ""}

    return {
        "available": window_pinner.available,
        "is_pinned": window_pinner.is_enabled,
        "title": window_pinner.title,
    }

if __name__ == "__main__":
    check_dependencies()
    print("\n" + "*"*60)
    print("   LOCAL CUA BACKEND SERVER READY (Port 8001)")
    print("   - Listening for Flutter requests on http://0.0.0.0:8001")
    print("   - Integration with agent_loop.py configured")
    print("*"*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8001)
