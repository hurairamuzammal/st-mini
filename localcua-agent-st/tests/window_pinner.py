import os
import ctypes
from dataclasses import dataclass

@dataclass
class PinResult:
    status: str
    detail: str

class WindowPinner:
    def __init__(self, title="LocalCUA"):
        self.title = title
        self.available = os.name == 'nt'
        self.is_enabled = False

    def pin(self) -> PinResult:
        if not self.available:
            return PinResult("unsupported", "Window pinning only supported on Windows")
        
        # Simple mock-like implementation for now
        # In a real scenario, this would use win32gui to set TOPMOST flag
        try:
            import win32gui
            import win32con
            hwnd = win32gui.FindWindow(None, self.title)
            if hwnd:
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, 
                                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                self.is_enabled = True
                return PinResult("success", f"Pinned window: {self.title}")
            return PinResult("error", f"Window not found: {self.title}")
        except Exception as e:
            return PinResult("error", f"Pinning failed: {str(e)}")

    def unpin(self) -> PinResult:
        if not self.available:
            return PinResult("unsupported", "Window pinning only supported on Windows")
        
        try:
            import win32gui
            import win32con
            hwnd = win32gui.FindWindow(None, self.title)
            if hwnd:
                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, 
                                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                self.is_enabled = False
                return PinResult("success", f"Unpinned window: {self.title}")
            return PinResult("error", f"Window not found: {self.title}")
        except Exception as e:
            return PinResult("error", f"Unpinning failed: {str(e)}")
