import os
import sys
import subprocess
import time

def check_dependencies():
    print("Checking dependencies...")
    try:
        import fastapi
        import uvicorn
        import pydantic
        print("Required packages are installed.")
    except ImportError as e:
        print(f"Missing dependency: {e.name}. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn", "pydantic"])

def start_server():
    backend_dir = os.path.join(os.path.dirname(__file__), "backend")
    main_file = os.path.join(backend_dir, "main.py")
    
    if not os.path.exists(main_file):
        print(f"Error: Could not find {main_file}")
        return

    print(f"Starting backend server on http://0.0.0.0:8001...")
    print("Make sure you have your VLM server (llama) running on port 8080.")
    
    try:
        # Run uvicorn directly to see output
        subprocess.check_call([
            sys.executable, "-m", "uvicorn", "backend.main:app", 
            "--host", "0.0.0.0", 
            "--port", "8001",
            "--reload"
        ])
    except KeyboardInterrupt:
        print("\nStopping server...")
    except Exception as e:
        print(f"Error starting server: {e}")

if __name__ == "__main__":
    check_dependencies()
    start_server()
